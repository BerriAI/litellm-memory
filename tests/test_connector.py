import json

import httpx
import pytest
import tomlkit

from moegent.connector import LocalCapture, configure_hosts, visible_message


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
        monkeypatch.setattr('moegent.connector.MAX_OUTBOX_BYTES', 1)
        assert local.capture(transcript, 'codex', 'session') == 0
        assert local.get('status') == 'outbox_full'
        monkeypatch.setattr('moegent.connector.MAX_OUTBOX_BYTES', 100000)
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
    assert len(hooks['Stop']) == 2
    assert hooks['Stop'][0]['hooks'][0]['command'] == 'existing hook'
    assert json.loads((tmp_path / '.claude.json').read_text())['theme'] == 'dark'
    before = (tmp_path / '.codex/hooks.json').read_text()
    with pytest.raises(ValueError, match='different litemem'):
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
    monkeypatch.setattr('moegent.connector.time.time', lambda: started)

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
        monkeypatch.setattr('moegent.connector.time.time', lambda: started + 30)
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
