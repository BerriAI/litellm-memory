import json

import httpx

from litellm_memory.imports import import_plan, legacy_plan


def source(root, folder, name, *, actor='moe-berri', content='Original.  Two spaces.\n'):
    path = root / folder / (name + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'id': name, 'title': 'Original title', 'content': content, 'actor': actor,
                                'created_at': '2025-06-01T10:20:00Z', 'scope': 'test', 'certainty': 'observed'}))
    return path


def test_legacy_import_preserves_files_dates_metadata_and_uploader_scope(tmp_path):
    original = source(tmp_path, 'journal/2025-06-01', 'record')
    source(tmp_path, 'archive/2025-06', 'record')
    source(tmp_path, 'journal/2025-06-01', 'other', actor='tin')
    knowledge = tmp_path / 'knowledge'
    knowledge.mkdir()
    (knowledge / 'decision.md').write_text('---\nid: decision\ntitle: Shared decision\nupdated_at: 2025-05-01T00:00:00Z\n---\n\nOriginal shared text.\n')
    raw = original.read_bytes()
    plan = legacy_plan(tmp_path, allowed_actors={'moe-berri'})
    assert len(plan.records) == 2 and plan.skipped == 2 and plan.invalid == 0
    record = next(r for r in plan.records if r.title == 'Original title')
    assert record.content == 'Original.  Two spaces.\n' and record.historical
    assert record.occurred_at.year == 2025
    assert record.metadata['original_file'].encode() == raw
    assert record.metadata['scope'] == 'test' and record.metadata['actor'] == 'moe-berri'
    assert original.read_bytes() == raw
    assert plan.manifest()['sources'][0]['sha256']


def test_import_retry_is_idempotent_and_auth_failures_leave_remaining_work(tmp_path):
    source(tmp_path, 'journal', 'one')
    source(tmp_path, 'journal', 'two')
    plan = legacy_plan(tmp_path, allowed_actors={'moe-berri'})
    seen = set()
    def handle(request):
        data = json.loads(request.content)
        assert data['historical'] and request.headers['authorization'] == 'Bearer token'
        duplicate = data['source_id'] in seen
        seen.add(data['source_id'])
        return httpx.Response(200, json={'id': data['source_id'], 'duplicate': duplicate})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert import_plan('https://memory.example', 'token', plan, client=client)['saved'] == 2
        assert import_plan('https://memory.example', 'token', plan, client=client)['duplicates'] == 2
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
        result = import_plan('https://memory.example', 'token', plan, client=client)
        assert result['failed'] == 1 and result['remaining'] == 1


def test_import_does_not_follow_symlinks_outside_repository(tmp_path):
    root = tmp_path / 'repo'
    (root / 'journal').mkdir(parents=True)
    external = source(tmp_path, 'outside', 'record')
    (root / 'journal' / 'link.json').symlink_to(external)
    plan = legacy_plan(root, allowed_actors={'moe-berri'})
    assert not plan.records and plan.skipped == 1
