"""Approve only the exact reminder commands reviewed during interactive setup."""

import json
import os
import selectors
import subprocess
import time
from pathlib import Path

import tomlkit

from .connector import atomic_private, reminder_command


def codex_hooks(home):
    process = subprocess.Popen(['codex', 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, bufsize=1,
                               env={**os.environ, 'CODEX_HOME': str(home / '.codex')})
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()

    send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'clientInfo': {'name': 'litellm-memory-setup', 'version': '1.0'},
        'capabilities': {'experimentalApi': True}}})
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not selector.select(1):
                    continue
                line = process.stdout.readline()
                if not line:
                    break
                message = json.loads(line)
                if message.get('id') == 1:
                    send({'jsonrpc': '2.0', 'method': 'initialized'})
                    send({'jsonrpc': '2.0', 'id': 2, 'method': 'hooks/list', 'params': {'cwds': [str(home)]}})
                if message.get('id') == 2:
                    if 'error' in message:
                        break
                    return [hook for entry in message['result']['data'] for hook in entry['hooks']]
        raise ValueError('Could not review Codex hooks. Open /hooks in Codex to finish setup.')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()


def trust_reviewed_codex_hooks(home, directory, *, list_hooks=codex_hooks):
    home = Path(home).resolve()
    expected = reminder_command(directory, 'codex')
    hooks = [hook for hook in list_hooks(home) if hook.get('command') == expected
             and hook['key'].startswith(str(home / '.codex/hooks.json') + ':')
             and hook['eventName'] in {'sessionStart', 'userPromptSubmit'} and not hook['isManaged']]
    if len(hooks) != 2 or {hook['eventName'] for hook in hooks} != {'sessionStart', 'userPromptSubmit'}:
        raise ValueError('The reviewed reminder hooks changed. Run setup again.')
    path = home / '.codex/config.toml'
    if path.is_symlink():
        raise ValueError('Review symlink-managed Codex hooks manually.')
    original = path.read_text()
    document = tomlkit.parse(original)
    states = document.setdefault('hooks', {}).setdefault('state', {})
    for hook in hooks:
        # Codex supplies its own hash; never guess it or grant blanket hook trust.
        states[hook['key']] = {'enabled': True, 'trusted_hash': hook['currentHash']}
    if path.read_text() != original:
        raise ValueError('Codex settings changed during review. Run setup again.')
    atomic_private(path.with_name(path.name + '.litellm-memory-backup-' + str(time.time_ns())), original)
    atomic_private(path, tomlkit.dumps(document))
    verified = {hook['key']: hook for hook in list_hooks(home)}
    if any(verified.get(hook['key'], {}).get('trustStatus') != 'trusted' for hook in hooks):
        raise ValueError('Codex has not confirmed hook trust. Open /hooks to finish setup.')
