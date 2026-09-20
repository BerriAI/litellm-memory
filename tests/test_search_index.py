import pytest
from test_archive import archive as archive_fixture

from moegent.archive import Reader, WorkInput

archive = archive_fixture


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
