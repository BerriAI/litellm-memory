import json

import httpx
import pytest
import tomlkit

from litellm_memory.connector import LocalCapture, configure_hosts, memory_hook, visible_message


def row(content, role='user', **extra):
    return {'type': 'response_item', 'timestamp': '2026-09-19T10:30:00Z', 'payload': {
        'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': content}], **extra}}


def test_transcript_capture_is_incremental_encrypted_and_replay_safe(tmp_path):
    transcript = tmp_path / 'work.jsonl'
    secret = 'PRIVATE_WORK_CANARY_482'
    rows = [json.dumps({'type': 'session_meta', 'payload': {'id': 'original-session'}}), json.dumps(row(secret)),
            json.dumps(row('Hidden private thought', 'assistant', channel='analysis')),
            json.dumps({'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'tool secret'}})]
    transcript.write_text('\n'.join(rows) + '\n' + json.dumps(row('partial'))[:-3])
    local = LocalCapture(tmp_path / 'state')
    try:
        assert local.capture(transcript, 'codex', 'file-session', historical=True) == 4
        assert local.capture(transcript, 'codex', 'original-session') == 4
        assert local.status()['queued'] == 1
        queued = local.db.execute('SELECT payload FROM outbox').fetchone()[0]
        assert secret not in queued
        record = local.vault.open_operational(queued)
        assert record['session_id'] == 'original-session'
        assert record['occurred_at'] == '2026-09-19T10:30:00+00:00'
        assert record['content'] == secret and record['historical'] is True
        assert record['metadata']['role'] == 'user'
        assert local.capture(transcript, 'codex', 'original-session') == 0
        with transcript.open('a') as handle:
            handle.write(json.dumps(row('partial'))[-3:] + '\n')
        assert local.capture(transcript, 'codex', 'original-session') == 1
        assert local.status()['queued'] == 2
        local.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        assert secret.encode() not in (tmp_path / 'state/capture.sqlite3').read_bytes()
    finally:
        local.close()


def test_claude_adapter_excludes_tools_reasoning_and_metadata():
    record = {'type': 'assistant', 'uuid': 'message-1', 'timestamp': '2026-09-19T01:00:00Z', 'message': {
        'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'hidden'},
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'secret'}}, {'type': 'text', 'text': 'Shipped API'}]}}
    assert visible_message(record, 'claude') == ('assistant', 'Shipped API', '2026-09-19T01:00:00+00:00', 'message-1')
    assert visible_message({**record, 'isMeta': True}, 'claude') is None
    assert visible_message({'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'content': 'full tool dump'}]}}, 'claude') is None


def test_codex_adapter_excludes_injected_user_role_context():
    context = row('Injected policy', internal_chat_message_metadata_passthrough={
        'content_item_kinds': ['agents_md.instructions']})
    assert visible_message(context, 'codex') is None
    mixed = row('Actual user text', internal_chat_message_metadata_passthrough={
        'content_item_kinds': ['user.text', 'environments.environment_context']})
    mixed['payload']['content'].append({'type': 'input_text', 'text': 'Synthetic environment context'})
    assert visible_message(mixed, 'codex')[1] == 'Actual user text'
    for prefix in ('<environment_context>', '# AGENTS.md instructions for /tmp/work', '<permissions instructions>'):
        assert visible_message(row(prefix + '\nInjected context'), 'codex') is None
    # Typed genuine user text remains genuine even when quoting an envelope.
    actual = row('<environment_context> quoted by the user', internal_chat_message_metadata_passthrough={
        'content_item_kinds': ['user.text']})
    assert visible_message(actual, 'codex') is not None


def test_connector_outage_rotation_idempotence_and_revoke_preserve_queue(tmp_path):
    local = LocalCapture(tmp_path)
    local.set('connection', {'url': 'https://memory.example', 'access_token': 'old', 'refresh_token': 'refresh',
                             'client_id': 'client', 'expires_at': 0})
    local.enqueue({'source_id': 'one', 'content': 'private'})
    local.db.commit()
    calls = []
    offline = [True]
    def gateway(request):
        calls.append(request.url.path)
        if request.url.path == '/oauth/token':
            return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'new-refresh', 'expires_in': 3600})
        assert request.url.path == '/api/memories'  # Recording never searches.
        assert request.headers['authorization'] == 'Bearer new'
        return httpx.Response(503 if offline[0] else 200, json={'id': 'record'})
    try:
        transport = httpx.MockTransport(gateway)
        assert local.flush(transport=transport) == 0
        assert local.status()['queued'] == 1
        assert local.get('connection')['refresh_token'] == 'new-refresh'
        offline[0] = False
        assert local.flush(transport=transport) == 1
        assert local.status()['queued'] == 0
        assert not local.enqueue({'source_id': 'one', 'content': 'private'})
        assert calls.count('/oauth/token') == 1
        local.enqueue({'source_id': 'two', 'content': 'later'})
        local.db.commit()
        assert local.flush(transport=httpx.MockTransport(lambda _: httpx.Response(401))) == 0
        assert local.status()['queued'] == 1 and local.status()['status'] == 'reconnect'
    finally:
        local.close()


def test_pause_skips_transcript_work_and_outbox_limit_does_not_advance_cursor(tmp_path, monkeypatch):
    local = LocalCapture(tmp_path / 'state')
    transcript = tmp_path / 'work.jsonl'
    transcript.write_text(json.dumps(row('before')) + '\n')
    try:
        local.capture(transcript, 'codex', 'session')
        local.set('paused', True)
        local.db.commit()
        with transcript.open('a') as file:
            file.write(json.dumps(row('during pause')) + '\n')
        local.capture(transcript, 'codex', 'session')
        local.set('paused', False)
        local.db.commit()
        with transcript.open('a') as file:
            file.write(json.dumps(row('after')) + '\n')
        monkeypatch.setattr('litellm_memory.connector.MAX_OUTBOX_BYTES', 1)
        assert local.capture(transcript, 'codex', 'session') == 0
        assert local.get('status') == 'outbox_full'
        monkeypatch.setattr('litellm_memory.connector.MAX_OUTBOX_BYTES', 100000)
        assert local.capture(transcript, 'codex', 'session') == 1
        captured = [local.vault.open_operational(r[0])['content'] for r in local.db.execute('SELECT payload FROM outbox')]
        assert captured == ['before', 'after']
    finally:
        local.close()


def test_installer_preserves_unrelated_configs_and_rejects_conflicts_before_writes(tmp_path):
    (tmp_path / '.codex').mkdir()
    original = '# Preserve my comments\nmodel = "custom"\n[mcp_servers.knowledge]\nurl = "https://old.example"\n'
    (tmp_path / '.codex/config.toml').write_text(original)
    (tmp_path / '.codex/hooks.json').write_text(json.dumps({'hooks': {'Stop': [{'hooks': [
        {'type': 'command', 'command': 'existing hook', 'timeout': 15}]}]}}))
    (tmp_path / '.claude.json').write_text(json.dumps({'theme': 'dark', 'mcpServers': {'old': {'type': 'stdio'}}}))
    for _ in range(2):
        configure_hosts(tmp_path, tmp_path / 'state', 'https://memory.example', ['codex', 'claude'])
    config = tomlkit.parse((tmp_path / '.codex/config.toml').read_text())
    assert config['model'] == 'custom' and config['mcp_servers']['knowledge']['url'] == 'https://old.example'
    assert '# Preserve my comments' in (tmp_path / '.codex/config.toml').read_text()
    hooks = json.loads((tmp_path / '.codex/hooks.json').read_text())['hooks']
    assert len(hooks['Stop']) == 1
    assert hooks['Stop'][0]['hooks'][0]['command'] == 'existing hook'
    assert len(hooks['UserPromptSubmit']) == 1
    assert json.loads((tmp_path / '.claude.json').read_text())['theme'] == 'dark'
    before = (tmp_path / '.codex/hooks.json').read_text()
    with pytest.raises(ValueError, match='different LiteLLM Memory'):
        configure_hosts(tmp_path, tmp_path / 'state', 'https://other.example', ['codex'])
    assert (tmp_path / '.codex/hooks.json').read_text() == before
    configure_hosts(tmp_path, tmp_path / 'state', 'https://memory.example', ['codex', 'claude'], remove=True)
    hooks = json.loads((tmp_path / '.codex/hooks.json').read_text())['hooks']
    assert list(hooks) == ['Stop'] and len(hooks['Stop']) == 1
    assert 'knowledge' in tomlkit.parse((tmp_path / '.codex/config.toml').read_text())['mcp_servers']
    assert (tmp_path / '.codex/hooks.json').stat().st_mode & 0o777 == 0o600


def test_capture_transactions_rollback_cursor_when_encrypt_fails(tmp_path, monkeypatch):
    local = LocalCapture(tmp_path / 'state')
    transcript = tmp_path / 'work.jsonl'
    transcript.write_text(json.dumps(row('captured once')) + '\n')
    original = local.enqueue
    def fail(_):
        raise RuntimeError('simulated interruption')
    monkeypatch.setattr(local, 'enqueue', fail)
    with pytest.raises(RuntimeError):
        local.capture(transcript, 'codex', 'session')
    monkeypatch.setattr(local, 'enqueue', original)
    try:
        local.capture(transcript, 'codex', 'session')
        assert local.status()['queued'] == 1
    finally:
        local.close()


def test_resume_skips_pause_without_hooks_and_preexisting_sessions(tmp_path, monkeypatch):
    local = LocalCapture(tmp_path / 'state')
    started = 1_000_000_000
    monkeypatch.setattr('litellm_memory.connector.time.time', lambda: started)

    def dated(content, seconds):
        from datetime import UTC, datetime
        value = row(content)
        value['timestamp'] = datetime.fromtimestamp(seconds, UTC).isoformat() if seconds else None
        return json.dumps(value) + '\n'

    transcript = tmp_path / 'active.jsonl'
    transcript.write_text(dated('old session text', started - 10))
    try:
        local.set_paused(False)
        local.db.commit()
        with transcript.open('a') as file:
            file.write(dated('before pause', started + 10))
        local.capture(transcript, 'codex', 'one')
        local.set_paused(True)
        local.db.commit()
        with transcript.open('a') as file:
            file.write(dated('paused without a hook', started + 20))
        monkeypatch.setattr('litellm_memory.connector.time.time', lambda: started + 30)
        local.set_paused(False)
        local.db.commit()
        # Resume must not depend on having received a hook while paused.
        with transcript.open('a') as file:
            file.write(dated('after resume', started + 40))
        local.capture(transcript, 'codex', 'one')
        other = tmp_path / 'newly-discovered.jsonl'
        other.write_text(dated('also paused', started + 20) + dated('undated', None))
        local.capture(other, 'codex', 'two')
        captured = [local.vault.open_operational(r[0])['content']
                    for r in local.db.execute('SELECT payload FROM outbox')]
        assert captured == ['before pause', 'after resume']
        assert local.status()['coverage_warning'] == 'undated_live_message_skipped'
        # A reconnect while active keeps the boundary and any unread backlog.
        local.set_paused(False)
        assert local.get('capture_since') == started + 30
        local.db.commit()
        # An explicitly requested historical import still accepts old
        # or undated text without changing originals or manufacturing dates.
        local.capture(other, 'codex', 'two', historical=True)
        assert local.status()['queued'] == 4
        assert local.capture(other, 'codex', 'two', historical=True) == 0
    finally:
        local.close()


def test_remote_pause_discard_leaves_explicit_backfill_possible(tmp_path):
    local = LocalCapture(tmp_path)
    local.set('connection', {'url': 'https://memory.example', 'access_token': 'token', 'expires_at': 10**20})
    record = {'source_id': 'paused-source', 'content': 'Paused text', 'historical': False}
    local.enqueue(record)
    local.db.commit()
    try:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
            'status': 'skipped', 'reason': 'outside_capture_window'}))
        assert local.flush(transport=transport) == 1
        assert local.status()['queued'] == 0
        assert local.status()['skipped_after_pause'] == 1
        # A successful non-storage acknowledgment must not become a receipt
        # that would prohibit a future deliberate historical import.
        assert local.enqueue({**record, 'historical': True})
        local.db.commit()
        assert local.status()['queued'] == 1
    finally:
        local.close()


def test_capture_state_survives_reopen(tmp_path, monkeypatch):
    from pathlib import Path

    from litellm_memory.connector import capture_directory

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert capture_directory() == tmp_path / '.local/share/litellm-memory'
    directory = capture_directory()
    local = LocalCapture(directory)
    local.set('account_binding', {'url': 'https://memory.example', 'owner': 'T:OWNER'})
    local.enqueue({'source_id': 'before-rename', 'content': 'private queued work'})
    local.db.commit()
    local.close()
    reopened = LocalCapture(capture_directory())
    try:
        assert reopened.directory == directory
        assert reopened.get('account_binding')['owner'] == 'T:OWNER'
        assert reopened.status()['queued'] == 1
        encrypted = reopened.db.execute('SELECT payload FROM outbox').fetchone()[0]
        assert reopened.vault.open_operational(encrypted)['content'] == 'private queued work'
    finally:
        reopened.close()


def test_reconnect_replaces_remote_auth_with_shared_bridge_without_duplicate_hooks(tmp_path):
    (tmp_path / '.codex').mkdir()
    (tmp_path / '.claude').mkdir()
    url = 'https://memory.example'
    (tmp_path / '.codex/config.toml').write_text(
        '[mcp_servers.litellm-memory]\nurl = "https://memory.example/mcp"\nbearer_token_env_var = "EXISTING_TOKEN"\n')
    (tmp_path / '.claude.json').write_text(json.dumps({'mcpServers': {
        'litellm-memory': {'type': 'http', 'url': url + '/mcp', 'headers': {'X-Preserved': 'yes'}}}}))
    old_hooks = {'hooks': {'Stop': [{'hooks': [
        {'type': 'command', 'command': 'python -m litellm_memory.connector hook --marker litellm-memory-managed-capture'},
        {'type': 'command', 'command': 'unrelated-hook'}]}]}}
    for path in (tmp_path / '.codex/hooks.json', tmp_path / '.claude/settings.json'):
        path.write_text(json.dumps(old_hooks))
    for _ in range(2):
        configure_hosts(tmp_path, tmp_path / 'state', url, ['codex', 'claude'])
    codex = tomlkit.parse((tmp_path / '.codex/config.toml').read_text())['mcp_servers']
    claude = json.loads((tmp_path / '.claude.json').read_text())['mcpServers']
    assert list(codex) == ['litellm-memory'] and 'bearer_token_env_var' not in codex['litellm-memory']
    assert codex['litellm-memory']['args'][-3:] == ['mcp', '--url', url]
    assert list(claude) == ['litellm-memory'] and 'headers' not in claude['litellm-memory']
    assert claude['litellm-memory']['type'] == 'stdio'
    assert claude['litellm-memory']['args'] == list(codex['litellm-memory']['args'])
    for path in (tmp_path / '.codex/hooks.json', tmp_path / '.claude/settings.json'):
        commands = [h['command'] for g in json.loads(path.read_text())['hooks']['Stop'] for h in g['hooks']]
        assert commands == ['unrelated-hook']
        prompts = json.loads(path.read_text())['hooks']['UserPromptSubmit']
        assert len(prompts) == 1
        assert '--marker litellm-memory-managed-capture' in prompts[0]['hooks'][0]['command']
    configure_hosts(tmp_path, tmp_path / 'state', url, ['codex', 'claude'], remove=True)
    assert not tomlkit.parse((tmp_path / '.codex/config.toml').read_text()).get('mcp_servers')
    assert not json.loads((tmp_path / '.claude.json').read_text())['mcpServers']
    for path in (tmp_path / '.codex/hooks.json', tmp_path / '.claude/settings.json'):
        assert json.loads(path.read_text())['hooks']['Stop'][0]['hooks'][0]['command'] == 'unrelated-hook'


def test_new_host_installations_use_canonical_name(tmp_path):
    configure_hosts(tmp_path, tmp_path / 'state', 'https://memory.example', ['codex', 'claude'])
    assert list(tomlkit.parse((tmp_path / '.codex/config.toml').read_text())['mcp_servers']) == ['litellm-memory']
    assert list(json.loads((tmp_path / '.claude.json').read_text())['mcpServers']) == ['litellm-memory']


def test_memory_hooks_are_advisory_selective_and_allow_empty_turns():
    for event in ('SessionStart', 'UserPromptSubmit'):
        output = memory_hook({'hook_event_name': event, 'prompt': 'PRIVATE_PROMPT'})
        assert output['hookSpecificOutput']['hookEventName'] == event
        context = output['hookSpecificOutput']['additionalContext']
        assert 'Keep a low bar' in context and 'Skip saving' in context
        assert 'PRIVATE_PROMPT' not in context
        assert 'decision' not in output  # Never block/force a continuation to create a memory.
        assert 'recording is paused' in memory_hook({'hook_event_name': event}, paused=True)['hookSpecificOutput']['additionalContext']
        assert memory_hook({'hook_event_name': event}, connected=False) == {}
    for event in ('Stop', 'PostToolUse', 'unknown'):
        assert memory_hook({'hook_event_name': event}) == {}


def test_hook_command_never_reads_transcripts_or_flushes_records(tmp_path, monkeypatch, capsys):
    import io

    from litellm_memory.connector import main

    directory = tmp_path / 'state'
    local = LocalCapture(directory)
    local.set('connection', {'url': 'https://memory.example'})
    local.enqueue({'source_id': 'explicit-import', 'content': 'QUEUED_IMPORT'})
    local.db.commit()
    local.close()
    def forbidden(*args, **kwargs):
        raise AssertionError('A reminder hook must not capture or upload data')
    monkeypatch.setattr(LocalCapture, 'capture', forbidden)
    monkeypatch.setattr(LocalCapture, 'flush', forbidden)
    monkeypatch.setattr('sys.argv', ['connector', '--directory', str(directory), 'hook', '--host', 'codex'])
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({
        'hook_event_name': 'UserPromptSubmit', 'transcript_path': '/must/not/read', 'prompt': 'PRIVATE_PROMPT'})))
    main()
    output = capsys.readouterr().out
    assert 'record_work' in json.loads(output)['hookSpecificOutput']['additionalContext']
    assert 'PRIVATE_PROMPT' not in output and 'QUEUED_IMPORT' not in output
    local = LocalCapture(directory)
    assert local.status()['queued'] == 1
    local.close()


def test_optional_agent_guidance_preserves_other_instructions_and_disconnect_removes_it(tmp_path):
    originals = {}
    for relative in ('.codex/AGENTS.md', '.claude/CLAUDE.md'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        originals[path] = '# My instructions\nKeep unrelated guidance, including product-team-knowledge.\n'
        path.write_text(originals[path])
    args = (tmp_path, tmp_path / 'state', 'https://memory.example', ['codex', 'claude'])
    configure_hosts(*args)
    assert all(path.read_text() == original for path, original in originals.items())
    for _ in range(2):
        configure_hosts(*args, agent_instructions=True)
    for path, original in originals.items():
        text = path.read_text()
        assert text.startswith(original)
        assert text.count('<!-- BEGIN litellm-memory-agent-instructions -->') == 1
        assert 'record_work' in text and 'Do not search automatically' in text
    # An ordinary reconnect must not silently opt out or duplicate instructions.
    before = {path: path.read_text() for path in originals}
    configure_hosts(*args)
    assert all(path.read_text() == text for path, text in before.items())
    configure_hosts(*args, remove=True)
    assert all(path.read_text() == original for path, original in originals.items())


def test_guidance_can_be_disabled_independently_and_malformed_markers_fail_before_writes(tmp_path):
    args = (tmp_path, tmp_path / 'state', 'https://memory.example', ['codex', 'claude'])
    configure_hosts(*args, agent_instructions=True)
    config = tmp_path / '.codex/config.toml'
    hooks = tmp_path / '.codex/hooks.json'
    before = (config.read_bytes(), hooks.read_bytes())
    configure_hosts(*args, agent_instructions=False, instructions_only=True)
    assert (config.read_bytes(), hooks.read_bytes()) == before
    assert not (tmp_path / '.codex/AGENTS.md').read_text()
    assert not (tmp_path / '.claude/CLAUDE.md').read_text()
    (tmp_path / '.claude/CLAUDE.md').write_text('<!-- BEGIN litellm-memory-agent-instructions -->\nUnfinished')
    with pytest.raises(ValueError, match='instruction markers'):
        configure_hosts(*args, agent_instructions=True)
    assert not (tmp_path / '.codex/AGENTS.md').read_text()
    assert (config.read_bytes(), hooks.read_bytes()) == before
