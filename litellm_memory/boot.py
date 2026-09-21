"""Portable launcher: optional local Museum, first-run provisioning, and the web app.

Only the web port is published. Museum stores ciphertext in Postgres; member
identity and encryption delegation are owned by LiteLLM Memory, not Ente's Photos UI.
"""

import asyncio
import base64
import contextlib
import os
import secrets
import signal
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import yaml

from .config import Settings
from .crypto import KeyVault
from .ente import Museum
from .setup import DeploymentConfig


class BootError(RuntimeError):
    pass


def database_config(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'postgres', 'postgresql'} or not parsed.hostname or not parsed.path.strip('/'):
        raise BootError('Set DATABASE_URL to the deployment’s PostgreSQL connection URL.')
    return {'host': parsed.hostname, 'port': parsed.port or 5432, 'name': unquote(parsed.path.lstrip('/')),
            'user': unquote(parsed.username or ''), 'password': unquote(parsed.password or ''),
            'sslmode': parse_qs(parsed.query).get('sslmode', ['require'])[0]}


def museum_config(database_url, server_secrets, *, bootstrap=False, port=9080):
    config = {'db': database_config(database_url), 'http': {'port': port}, 'log-level': 'warn',
              'key': {'encryption': server_secrets['encryption'], 'hash': server_secrets['hash']},
              'jwt': {'secret': server_secrets['jwt']},
              'internal': {'silent': True, 'disable-registration': not bootstrap, 'admins': []}}
    if bootstrap:
        config['internal']['hardcoded-ott'] = {'emails': [server_secrets['email'] + ',' + server_secrets['ott']]}
    return config


async def provision_account(base_url, server_secrets, transport=None):
    async with httpx.AsyncClient(base_url=base_url, timeout=30, transport=transport,
                                 headers={'X-Client-Package': 'io.ente.photos'}) as client:
        response = await client.post('/users/ott', json={'email': server_secrets['email'], 'purpose': 'signup'})
        if not response.is_success:
            raise BootError(f'Encrypted storage account initialization failed (HTTP {response.status_code}).')
        response = await client.post('/users/verify-email', json={
            'email': server_secrets['email'], 'ott': server_secrets['ott'], 'source': 'litellm-memory'})
        if not response.is_success:
            raise BootError(f'Encrypted storage account verification failed (HTTP {response.status_code}).')
        data = response.json()
        if not isinstance(data.get('token'), str) or not data['token']:
            raise BootError('Encrypted storage did not return a service credential.')
        return data['token']


async def stop(process):
    if not process or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 20)
    except TimeoutError:
        process.kill()
        await process.wait()


async def wait_for_museum(base_url, process):
    async with httpx.AsyncClient(timeout=3) as client:
        for _ in range(90):
            if process.returncode is not None:
                raise BootError('Encrypted storage exited during startup. Check the PostgreSQL connection.')
            try:
                if (await client.get(base_url + '/ping')).is_success:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2)
    raise BootError('Encrypted storage did not become ready. Check the PostgreSQL connection and try again.')


async def start_museum(settings, vault, deployment):
    database_url = os.environ.get('DATABASE_URL', '')
    directory = Path(os.environ.get('MUSEUM_DIRECTORY', '/opt/ente'))
    base_url = 'http://127.0.0.1:9080'
    credentials_path = settings.state_dir / 'museum.yaml'
    server_secrets = deployment.values.get('museum_server')
    if not server_secrets:
        server_secrets = {'encryption': base64.b64encode(secrets.token_bytes(32)).decode(),
                          'hash': base64.b64encode(secrets.token_bytes(64)).decode(),
                          'jwt': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                          'email': 'litellm-memory-service@localhost.internal', 'ott': secrets.token_urlsafe(40)}
        deployment.save(museum_server=server_secrets)
    token = deployment.values.get('museum_token')

    async def launch(bootstrap):
        config = museum_config(database_url, server_secrets, bootstrap=bootstrap)
        deployment._atomic(credentials_path, yaml.safe_dump(config, sort_keys=False))
        environment = {**os.environ, 'ENTE_CREDENTIALS_FILE': str(credentials_path.resolve())}
        # Museum logging is not part of application observability. Bootstrap
        # requests and auth credentials must never enter container logs.
        process = await asyncio.create_subprocess_exec(str(directory / 'museum'), cwd=directory, env=environment,
                                                       stdout=asyncio.subprocess.DEVNULL,
                                                       stderr=asyncio.subprocess.DEVNULL)
        try:
            await wait_for_museum(base_url, process)
        except BaseException:
            await stop(process)
            raise
        return process

    process = await launch(bootstrap=not token)
    try:
        if not token:
            token = await provision_account(base_url, server_secrets)
            # Save the result before closing bootstrap. A restart resumes with
            # registration disabled, without requiring any member to handle keys.
            deployment.save(museum_token=token)
            await stop(process)
            process = await launch(bootstrap=False)
        async with contextlib.AsyncExitStack() as stack:
            museum = Museum(base_url, token, namespace_key=vault.namespace_key())
            stack.push_async_callback(museum.close)
            await museum.ensure_namespace()
    except BaseException:
        await stop(process)
        raise
    os.environ['ENTE_BASE_URL'], os.environ['ENTE_AUTH_TOKEN'] = base_url, token
    return process


async def serve():
    if not os.environ.get('PUBLIC_BASE_URL') and os.environ.get('RENDER_EXTERNAL_URL'):
        os.environ['PUBLIC_BASE_URL'] = os.environ['RENDER_EXTERNAL_URL']
    settings = Settings()
    settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    vault = KeyVault(settings.state_dir, settings.vault_wrapping_key)
    deployment = DeploymentConfig(settings, vault)
    processes = []
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        if not (settings.ente_base_url and settings.ente_auth_token):
            processes.append(await start_museum(settings, vault, deployment))
        web = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'uvicorn', 'litellm_memory.application:app', '--host', '0.0.0.0',
            '--port', os.environ.get('PORT', '8000'), '--no-access-log', '--log-level', 'warning')
        processes.append(web)
        print('LiteLLM Memory is starting. Finish setup in the web app. Setup code: private STATE_DIR/setup-code file.', flush=True)
        waiting = [asyncio.create_task(process.wait()) for process in processes]
        waiting.append(asyncio.create_task(stopping.wait()))
        done, pending = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if not stopping.is_set() and any(p.returncode not in {None, 0} for p in processes):
            raise BootError('A required service stopped. The process supervisor will restart LiteLLM Memory.')
    finally:
        for process in reversed(processes):
            await stop(process)


def main():
    try:
        asyncio.run(serve())
    except BootError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        # Never print upstream payloads, URLs with credentials, or tracebacks.
        print(f'LiteLLM Memory could not start ({type(error).__name__}). Check deployment configuration.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
