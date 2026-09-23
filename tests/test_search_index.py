import pytest
from test_archive import archive as archive_fixture

from litellm_memory.archive import Reader, WorkInput

archive = archive_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['', 'update'])
async def test_date_search_includes_live_recorded_work_without_redating_imports(archive, monkeypatch, query):
    from litellm_memory.time_settings import activity_timestamp

    monkeypatch.setattr('litellm_memory.archive.now', lambda: '2026-09-23T01:40:00+00:00')
    included = set()
    for name, options in [
        ('codex', {'source': 'codex'}),
        ('claude', {'source': 'claude'}),
        ('mcp', {'source': 'mcp'}),
        ('event-in-range', {'source': 'codex', 'occurred_at': '2026-09-22T07:00:00Z'}),
        ('older-event', {'source': 'codex', 'occurred_at': '2026-09-22T06:59:59Z'}),
        ('next-day-event', {'source': 'codex', 'occurred_at': '2026-09-23T07:00:00Z'}),
        ('historical', {'source': 'codex', 'historical': True}),
        ('timezone-unknown', {'source': 'codex', 'occurred_at': '2026-09-22T12:00:00'}),
        ('manual', {}),
    ]:
        saved = await archive.capture('T1:A', WorkInput(source_id=name, content='Work update', **options))
        if name in {'codex', 'claude', 'mcp', 'event-in-range'}:
            included.add(saved['id'])
        await archive.set_visibility('T1:A', saved['id'], 'public')
        row = await archive.next_work(historical=options.get('historical', False))
        payload = await archive.decode(row)
        payload.update(summary='Shared update', labels={'progress': .95})
        await archive.complete_work(row, payload, private_match=False)
    private = await archive.capture('T1:A', WorkInput(source='codex', source_id='private', content='Private update'))
    bounds = {'created_from': '2026-09-22T07:00:00Z', 'created_before': '2026-09-23T07:00:00Z'}
    for reader in [Reader('T1:A'), Reader('T1:A', 'public'), Reader('T1:B')]:
        found = await archive.search(reader, query, owner='T1:A', **bounds)
        assert {r['id'] for r in found['results']} == included | ({private['id']} if reader.audience == 'owner'
                                                                 and reader.owner == 'T1:A' else set())
        for item in found['results']:
            assert item['activity_at'] == activity_timestamp(item).isoformat()
            assert item['date_basis'] == ('event' if item['created_at'] else 'recorded')
            if item['date_basis'] == 'recorded':
                assert item['created_at'] is None
                assert item['activity_at'] == item['captured_at']
    assert not (await archive.search(Reader('T2:C'), query, **bounds))['results']
    original = await archive.read(Reader('T1:A'), private['id'])
    assert original['created_at'] is None and original['original']['occurred_at'] is None


@pytest.mark.asyncio
async def test_recorded_time_search_uses_inclusive_start_and_exclusive_end(archive, monkeypatch):
    ids = []
    for index, captured in enumerate(['2026-09-22T06:59:59Z', '2026-09-22T07:00:00Z',
                                     '2026-09-23T06:59:59Z', '2026-09-23T07:00:00Z']):
        monkeypatch.setattr('litellm_memory.archive.now', lambda: captured)
        ids.append((await archive.capture('T1:A', WorkInput(source='codex', source_id=str(index),
                                                           content='Live work')))['id'])
    found = await archive.search(Reader('T1:A'), created_from='2026-09-22T00:00:00-07:00',
                                 created_before='2026-09-23T00:00:00-07:00')
    assert [r['id'] for r in found['results']] == [ids[2], ids[1]]


@pytest.mark.asyncio
async def test_search_reuses_unchanged_partitions_and_invalidates_deleted_content(archive):
    first = await archive.capture('T1:A', WorkInput(source_id='cached', content='SEARCH_PRIVATE_CANARY',
                                                  occurred_at='2026-09-18T12:00:00Z'))
    await archive.capture('T1:B', WorkInput(source_id='different', content='ANOTHER_PERSON_PRIVATE_CANARY'))
    results = await archive.search(Reader('T1:A'), 'SEARCH_PRIVATE_CANARY')
    assert results['results'][0]['id'] == first['id']
    built = archive.search_index.builds
    await archive.search(Reader('T1:A'), 'SEARCH_PRIVATE_CANARY')
    assert archive.search_index.builds == built
    assert all(key[0] == 'T1:A' for key in archive.search_index.cache)
    await archive.delete('T1:A', first['id'])
    assert not archive.search_index.cache
    assert not (await archive.search(Reader('T1:A'), 'SEARCH_PRIVATE_CANARY'))['results']


@pytest.mark.asyncio
async def test_partition_budget_eviction_keeps_search_correct_and_no_plaintext_disk_index(archive):
    for number in range(10):
        await archive.capture('T1:A', WorkInput(source_id=str(number), title=f'Item {number}',
                                              content='Budget search original content ' + str(number),
                                              occurred_at=f'2026-09-{number+1:02}T12:00:00Z'))
    archive.search_index.budget = 45_000
    result = await archive.search(Reader('T1:A'), '', offset=2, limit=3)
    assert [r['title'] for r in result['results']] == ['Item 7', 'Item 6', 'Item 5']
    assert result['total'] == 10 and result['next_offset'] == 5
    assert archive.search_index.used <= archive.search_index.budget
    found = await archive.search(Reader('T1:A'), 'Budget', limit=30)
    assert found['total'] == 10
    assert archive.search_index.used <= archive.search_index.budget
    # Persistent state contains encrypted documents/vectors, never an FTS spill.
    tables = {r[0] for r in await (await archive.db.execute("SELECT name FROM sqlite_master")).fetchall()}
    assert 'work' not in tables
    await archive.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert b'Budget search original content' not in archive.path.read_bytes()


@pytest.mark.asyncio
async def test_public_index_only_searches_public_summary_even_after_private_cache_warm(archive):
    saved = await archive.capture('T1:A', WorkInput(source_id='dual-index', content='PRIVATE_NEVER_PUBLISHED'))
    await archive.set_visibility('T1:A', saved['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(summary='PUBLISHED_SUMMARY', labels={'progress': 0.95})
    await archive.complete_work(row, payload, private_match=False)
    assert (await archive.search(Reader('T1:A'), 'PRIVATE_NEVER_PUBLISHED'))['total'] == 1
    assert (await archive.search(Reader('T1:B'), 'PRIVATE_NEVER_PUBLISHED'))['total'] == 0
    assert (await archive.search(Reader('T1:B'), 'PUBLISHED_SUMMARY'))['total'] == 1
    await archive.add_rule('T1:A', 'A restricted topic')
    assert (await archive.search(Reader('T1:B'), 'PUBLISHED_SUMMARY'))['total'] == 0


@pytest.mark.asyncio
async def test_metadata_scan_keeps_owner_and_public_scopes_disjoint_and_requires_a_publication(archive):
    shared = await archive.capture('T1:A', WorkInput(source_id='shared', content='ORIGINAL_PRIVATE_CANARY'))
    await archive.set_visibility('T1:A', shared['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(summary='A shared summary', labels={'progress': .95})
    await archive.complete_work(row, payload, private_match=False)
    private = await archive.capture('T1:B', WorkInput(source_id='private', content='B_ONLY_PRIVATE_CANARY'))

    async def ids(reader, **scope):
        result = await archive.search(reader, **scope)
        identifiers = [item['id'] for item in result['results']]
        assert result['total'] == len(set(identifiers))  # No owner/public duplicate branch.
        return set(identifiers)

    assert await ids(Reader('T1:A')) == {shared['id']}
    assert await ids(Reader('T1:A', 'public')) == {shared['id']}
    assert await ids(Reader('T1:B')) == {shared['id'], private['id']}
    assert await ids(Reader('T1:B', 'public')) == {shared['id']}
    assert await ids(Reader('T1:A'), owner='T1:B') == set()
    assert await ids(Reader('T2:C')) == set()
    archive.sharing_enabled = False
    assert await ids(Reader('T1:B')) == {private['id']}
    assert await ids(Reader('T1:A', 'public')) == set()
    archive.sharing_enabled = True
    # Even if visibility is inconsistent, a missing separate summary must remove
    # the row from public searches, including already-warmed partitions. It must
    # never fall back to the owner's encrypted original.
    await archive.db.execute('UPDATE records SET publication=NULL WHERE id=?', (shared['id'],))
    await archive.db.commit()
    assert await ids(Reader('T1:B')) == {private['id']}
    assert await ids(Reader('T1:A', 'public')) == set()
    assert await ids(Reader('T1:A')) == {shared['id']}
    assert not (await archive.search(Reader('T1:B'), 'ORIGINAL_PRIVATE_CANARY'))['results']
