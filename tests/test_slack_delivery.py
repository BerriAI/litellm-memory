import asyncio
from unittest.mock import AsyncMock

import pytest
from test_archive import archive as archive_fixture

from litellm_memory.archive import Reader
from litellm_memory.config import Settings
from litellm_memory.gateway import Gateway
from litellm_memory.progress import report
from litellm_memory.query import CitationError
from litellm_memory.query_worker import query_worker
from litellm_memory.slack import Slack
from litellm_memory.storage import Storage

archive = archive_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_slack_only_delivers_acknowledgment_and_validated_final(archive, tmp_path, fail):
    settings = Settings(_env_file=None, state_dir=tmp_path, agent_timeout_seconds=10)
    store = Storage(tmp_path / 'query-test.sqlite3')
    await store.open()
    gateway = Gateway(settings)
    slack = Slack(settings, store)
    slack.permitted = AsyncMock(return_value=True)
    slack.reader = AsyncMock(return_value=Reader('T1:A'))
    slack.history = AsyncMock(return_value=[])
    slack.api = AsyncMock(return_value={'ok': True, 'ts': '99'})

    async def agent(*args, **kwargs):
        assert slack.api.await_count == 1  # Acknowledged before any model work.
        await report('Writing an answer', 'UNVERIFIED answer fragment')
        if fail:
            raise CitationError('Invalid source')
        return 'A completed, verified answer.'

    payload = dict(team='T1', channel='C1', user='A', thread_ts='1', question='What is happening?')
    jid = await store.enqueue('T1:A', 'slack', payload)
    worker = asyncio.create_task(query_worker(settings, store, slack, archive, gateway, {}, agent))
    try:
        for _ in range(100):
            record = await store.get_job(jid)
            if record['status'] in {'done', 'failed'}:
                break
            await asyncio.sleep(.01)
        assert record['status'] == ('failed' if fail else 'done')
        await slack.indicator(jid)
        calls = slack.api.call_args_list
        assert [c.args[0] for c in calls] == ['chat.postMessage', 'chat.update']
        assert all('UNVERIFIED' not in str(c) for c in calls)
        assert 'reply here when the answer is ready' in calls[0].args[1]['text']
        assert not record['partial']
        assert 'could not be verified' in calls[1].args[1]['text'] if fail else calls[1].args[1]['text'] == 'A completed, verified answer.'
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await gateway.close()
        await store.close()
