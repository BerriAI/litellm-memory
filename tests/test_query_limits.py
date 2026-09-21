import asyncio

import pytest
from test_archive import archive as archive_fixture

from moegent.archive import Reader
from moegent.config import Settings
from moegent.storage import QueueFull, Storage

archive = archive_fixture


@pytest.mark.asyncio
async def test_pending_query_quota_is_atomic_durable_and_does_not_reject_idempotent_retry(tmp_path):
    store = Storage(tmp_path / 'queries.sqlite3')
    await store.open()
    try:
        results = await asyncio.gather(*(store.enqueue('T:A', 'web', {'question': 'synthetic'}, str(i))
                                        for i in range(10)), return_exceptions=True)
        accepted = [r for r in results if isinstance(r, str)]
        assert len(accepted) == 4 and sum(isinstance(r, QueueFull) for r in results) == 6
        assert await store.enqueue('T:A', 'web', {'question': 'synthetic'}, '0') == accepted[0]
        assert await store.enqueue('T:B', 'slack', {'question': 'other person'})
        await store.cancel(accepted[0])
        assert await store.enqueue('T:A', 'web', {'question': 'replacement'})
    finally:
        await store.close()
    await store.open()
    try:
        with pytest.raises(QueueFull):
            await store.enqueue('T:A', 'web', {'question': 'after restart'})
        store.max_pending = 5
        with pytest.raises(QueueFull):
            await store.enqueue('T:C', 'web', {'question': 'global limit'})
        # Channel departure is an access operation and must never be quota-blocked.
        assert await store.enqueue('T:A', 'leave', {})
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_web_and_mcp_answers_share_capacity_and_cancel_releases_it(archive, monkeypatch):
    from moegent import query
    entered, peak, calls = 0, 0, 0
    release = asyncio.Event()
    async def answering(*args):
        nonlocal entered, peak, calls
        calls += 1
        entered += 1
        peak = max(peak, entered)
        try:
            await release.wait()
            return 'done'
        finally:
            entered -= 1
    monkeypatch.setattr(query, '_answer_work', answering)
    settings = Settings(_env_file=None)
    tasks = [asyncio.create_task(query.answer_work(settings, archive, object(), Reader('T1:A'), 'question'))
             for _ in range(4)]
    try:
        for _ in range(20):
            if calls == 2:
                break
            await asyncio.sleep(.01)
        assert calls == 2 and peak == 2
        with pytest.raises(QueueFull):
            await query.answer_work(settings, archive, object(), Reader('T1:A'), 'one more')
        tasks[-1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[-1]
        release.set()
        assert await asyncio.gather(*tasks[:-1]) == ['done'] * 3
        assert not archive.query_waiting and peak == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
