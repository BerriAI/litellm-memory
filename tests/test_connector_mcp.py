import asyncio
import json
import socket
import sys
import time

import pytest
import tomlkit
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_model_settings import application

from litellm_memory.connector import LocalCapture, configure_hosts, reminder_command
from litellm_memory.connector_hooks import trust_reviewed_codex_hooks
from litellm_memory.connector_mcp import describe


def test_hook_review_trusts_only_exact_commands_and_preserves_other_settings(tmp_path):
    state = tmp_path / 'state'
    configure_hosts(tmp_path, state, 'https://memory.example', ['codex'])
    path = tmp_path / '.codex/config.toml'
    hook_source = str(tmp_path / '.codex/hooks.json')
    expected = reminder_command(state, 'codex')
    calls = []

    def hooks(home):
        calls.append(home)
        return [{'key': hook_source + ':' + event, 'command': expected, 'eventName': event,
                 'isManaged': False, 'currentHash': 'sha256:' + event,
                 'trustStatus': 'trusted' if len(calls) > 1 else 'untrusted'}
                for event in ['sessionStart', 'userPromptSubmit']] + [
                    {'key': 'unrelated', 'command': expected + ' && unreviewed-command',
                     'eventName': 'sessionStart', 'isManaged': False}]

    trust_reviewed_codex_hooks(tmp_path, state, list_hooks=hooks)
    config = tomlkit.parse(path.read_text())
    assert len(config['hooks']['state']) == 2
    assert 'unrelated' not in config['hooks']['state']
    assert config['mcp_servers']['litellm-memory']['command'] == sys.executable
    assert len(calls) == 2


def test_hook_review_refuses_changed_command_without_mutating_config(tmp_path):
    state = tmp_path / 'state'
    configure_hosts(tmp_path, state, 'https://memory.example', ['codex'])
    path = tmp_path / '.codex/config.toml'
    original = path.read_text()
    with pytest.raises(ValueError, match='changed'):
        trust_reviewed_codex_hooks(tmp_path, state, list_hooks=lambda _: [])
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_two_stdio_clients_share_one_encrypted_login_and_save_through_real_mcp(tmp_path):
    app = application(tmp_path / 'server', [])
    async with app.router.lifespan_context(app):
        archive = app.state.archive
        await archive.enroll('T:OWNER', {'name': 'Owner'})
        token = await archive.create_token('T:OWNER', 'Shared connector')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        app.state.settings.public_base_url = url
        server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error', access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        local = LocalCapture(tmp_path / 'client')
        local.set('account_binding', {'url': url, 'owner': 'T:OWNER'})
        local.set('connection', {'url': url, 'owner': 'T:OWNER', 'access_token': token['token'],
                                'expires_at': time.time() + 3600})
        local.set('status', 'connected')
        local.db.commit()
        local.close()
        try:
            while not server.started:
                await asyncio.sleep(.01)
            local = LocalCapture(tmp_path / 'client')
            local.set('mcp_catalog', await describe(url, token['token']))
            local.db.commit()
            local.close()

            async def coding_client(host):
                params = StdioServerParameters(command=sys.executable, args=[
                    '-m', 'litellm_memory.connector', '--directory', str(tmp_path / 'client'), 'mcp', '--url', url])
                async with stdio_client(params) as streams:
                    async with ClientSession(*streams) as session:
                        initialized = await session.initialize()
                        assert 'Save useful work' in initialized.instructions
                        tools = await session.list_tools()
                        assert 'record_work' in {tool.name for tool in tools.tools}
                        result = await session.call_tool('record_work', {
                            'source_id': host + '-first', 'source': host, 'content': 'Testing my first memory.'})
                        assert not result.is_error, result
                        saved = json.loads(result.content[0].text)
                        read = await session.call_tool('read_memory', {'record_id': saved['id']})
                        assert not read.is_error and 'Testing my first memory.' in read.content[0].text
                        return saved['id']

            ids = await asyncio.gather(coding_client('codex'), coding_client('claude'))
            assert len(set(ids)) == 2
            assert (await archive.recording_status('T:OWNER'))['received'] == 2
            assert len(await archive.tokens('T:OWNER')) == 1
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)


def test_parallel_bridge_requests_refresh_once_without_flushing_history(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    from litellm_memory import connector
    from litellm_memory.connector_mcp import credentials

    local = LocalCapture(tmp_path)
    local.set('account_binding', {'url': 'https://memory.example', 'owner': 'T:OWNER'})
    local.set('connection', {'url': 'https://memory.example', 'owner': 'T:OWNER', 'client_id': 'client',
                            'access_token': 'old', 'refresh_token': 'refresh', 'expires_at': 0})
    local.enqueue({'source_id': 'old-history', 'content': 'Do not upload this during a memory call.'})
    local.db.commit()
    local.close()
    requests = []
    original_client = httpx.Client

    def remote(request):
        requests.append(request.url.path)
        assert request.url.path == '/oauth/token'
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'rotated', 'expires_in': 3600})

    def client(**kwargs):
        kwargs['transport'] = httpx.MockTransport(remote)
        return original_client(**kwargs)

    monkeypatch.setattr(connector.httpx, 'Client', client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: credentials(tmp_path, 'https://memory.example'), range(2))) == ['new', 'new']
    assert requests == ['/oauth/token']
    local = LocalCapture(tmp_path)
    assert local.status()['queued'] == 1
    local.close()
    with pytest.raises(ValueError):
        credentials(tmp_path, 'https://another-installation.example')
    assert requests == ['/oauth/token']


def test_desktop_codex_is_found_without_terminal_path(tmp_path, monkeypatch):
    from litellm_memory import connector_hooks

    monkeypatch.setattr(connector_hooks.shutil, 'which', lambda _: None)
    monkeypatch.setattr(connector_hooks.sys, 'platform', 'darwin')
    binary = tmp_path / 'Applications/ChatGPT.app/Contents/Resources/codex'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o700)
    assert connector_hooks.codex_executable(tmp_path) == str(binary)


def test_missing_codex_fails_before_browser_approval_or_host_changes(tmp_path, monkeypatch, capsys):
    from litellm_memory import connector, connector_hooks

    monkeypatch.setattr(connector_hooks.shutil, 'which', lambda _: None)
    monkeypatch.setattr(connector_hooks.sys, 'platform', 'linux')
    monkeypatch.setattr(connector, 'login', lambda *a, **kw: pytest.fail('must not open approval'))
    monkeypatch.setattr(sys, 'argv', ['connector', '--directory', str(tmp_path / 'state'),
                                    'connect', 'https://memory.example'])
    with pytest.raises(SystemExit) as error:
        connector.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert 'Codex was not found' in output.err and '--host claude' in output.err
    assert '/oauth/authorize' not in output.out


def test_failed_hook_verification_restores_both_hosts(tmp_path):
    from litellm_memory.connector import ConnectionSetupError

    paths = [tmp_path / path for path in ['.codex/config.toml', '.codex/hooks.json',
                                         '.claude.json', '.claude/settings.json']]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('theme = "system"\n' if path.suffix == '.toml' else '{"unrelated": true}\n')
    before = {path: path.read_text() for path in paths}
    expected = reminder_command(tmp_path / 'state', 'codex')

    def hooks(home):
        return [{'key': str(tmp_path / '.codex/hooks.json') + ':' + event, 'command': expected,
                 'eventName': event, 'isManaged': False, 'currentHash': 'sha256:' + event,
                 'trustStatus': 'untrusted'} for event in ['sessionStart', 'userPromptSubmit']]

    with pytest.raises(ConnectionSetupError, match='not confirmed'):
        configure_hosts(tmp_path, tmp_path / 'state', 'https://memory.example', ['codex', 'claude'],
                        agent_instructions=True, verify=lambda: trust_reviewed_codex_hooks(
                            tmp_path, tmp_path / 'state', list_hooks=hooks))
    assert {path: path.read_text() for path in paths} == before
    assert not (tmp_path / '.codex/AGENTS.md').exists()
    assert not (tmp_path / '.claude/CLAUDE.md').exists()


def test_host_rollback_preserves_concurrent_edits(tmp_path):
    from litellm_memory.connector import ConnectionSetupError

    path = tmp_path / '.claude.json'

    def fail_after_user_edit():
        path.write_text('{"user_changed_this": true}\n')
        raise ConnectionSetupError('Failed verification')

    with pytest.raises(ConnectionSetupError):
        configure_hosts(tmp_path, tmp_path / 'state', 'https://memory.example', ['codex', 'claude'],
                        verify=fail_after_user_edit)
    assert json.loads(path.read_text()) == {'user_changed_this': True}
    assert not (tmp_path / '.codex/config.toml').exists()


def test_disconnect_cleans_partial_setup_in_both_hosts_without_credentials(tmp_path, monkeypatch):
    from pathlib import Path

    from litellm_memory import connector

    directory = tmp_path / 'state'
    configure_hosts(tmp_path, directory, 'https://memory.example', ['codex', 'claude'], agent_instructions=True)
    local = LocalCapture(directory)
    local.set('account_binding', {'url': 'https://memory.example', 'owner': 'T:OWNER'})
    local.set('hosts', ['codex'])  # The failed install did not commit its new host list.
    local.db.commit()
    local.close()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(sys, 'argv', ['connector', '--directory', str(directory), 'disconnect'])
    connector.main()
    assert 'litellm-memory' not in tomlkit.parse((tmp_path / '.codex/config.toml').read_text()).get('mcp_servers', {})
    assert 'litellm-memory' not in json.loads((tmp_path / '.claude.json').read_text())['mcpServers']
    for filename in ['.codex/hooks.json', '.claude/settings.json', '.codex/AGENTS.md', '.claude/CLAUDE.md']:
        assert 'litellm-memory' not in (tmp_path / filename).read_text()
