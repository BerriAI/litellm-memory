import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from test_archive import archive as archive_fixture
from test_model_settings import application, enroll
from test_work_views import ViewGateway, ready

from litellm_memory.archive import Reader, WorkInput
from litellm_memory.config import Settings
from litellm_memory.view_preferences import ViewPreferences
from litellm_memory.work_views import ViewRequest, WorkViews

archive = archive_fixture


@pytest.mark.asyncio
async def test_daily_input_contains_every_full_memory_in_event_order(archive):
    ids = []
    # Exceeds the old 120-source ceiling; source text differs from derived excerpts.
    for index in reversed(range(125)):
        saved = await archive.capture('T1:A', WorkInput(source_id=f'full-{index}',
            content=f'Full evidence {index}: ' + ('detail ' * 500),
            occurred_at=f'2026-09-18T{index // 60:02}:{index % 60:02}:00Z'))
        row = await archive.next_work()
        payload = await archive.decode(row)
        payload.update(summary='SHORT_EXCERPT', projects=[])
        await archive.complete_work(row, payload, private_match=False)
        ids.append(saved['id'])
    gateway = ViewGateway()
    # Keep the synthetic output short, while retaining the actual full prompt for assertions.
    async def generate(messages, **kwargs):
        context = json.loads(messages[-1]['content'])
        gateway.inputs.append(context)
        assert kwargs['timeout'] == 600
        assert kwargs.get('max_tokens') is None  # Reasoning must not truncate the final JSON.
        return json.dumps({'items': [{'text': 'A day of recorded work.', 'sources': [context['evidence'][0]['id']]}]})
    gateway.generate = generate
    views = WorkViews(archive, gateway, SimpleNamespace(values={}))
    await views.open()
    request = ViewRequest(kind='daily', day='2026-09-18')
    await views.get(Reader('T1:A'), 'T1:A', request)
    await views.build(await views.next())
    evidence = gateway.inputs[0]['evidence']
    assert [r['id'] for r in evidence] == list(reversed(ids))
    assert all(r['summary'].startswith(f'Full evidence {i}:') and len(r['summary']) > 3000
               for i, r in enumerate(evidence))
    result = await views.get(Reader('T1:A'), 'T1:A', request)
    assert result['view']['coverage']['included'] == 125
    assert result['view']['coverage']['older_not_included'] == 0
    assert result['view']['through'] == '2026-09-18T02:04:00+00:00'
    assert await views.next() is None


@pytest.mark.asyncio
async def test_date_browser_only_enumerates_accessible_days_and_respects_filters(archive):
    await ready(archive, 'private', 'PRIVATE_DAY_CANARY', '2026-09-17T12:00:00Z')
    shared = await ready(archive, 'public', 'Shared work', '2026-09-18T12:00:00Z', public=True)
    views = WorkViews(archive, ViewGateway(), SimpleNamespace(values={}))
    await views.open()
    public = await views.daily(Reader('T1:B'), owner='T1:A')
    assert public['days'] == ['2026-09-18'] and public['day'] == '2026-09-18'
    assert len(public['summaries']) == 1
    await views.build(await views.next())
    public = await views.daily(Reader('T1:B'), owner='T1:A')
    assert public['summaries'][0]['view']['source_ids'] == [shared]
    assert 'PRIVATE_DAY_CANARY' not in json.dumps(public)
    assert (await views.daily(Reader('T1:A'), owner='T1:A'))['days'] == ['2026-09-18', '2026-09-17']
    assert (await views.daily(Reader('T1:A', 'public'), owner='T1:A'))['days'] == ['2026-09-18']
    await archive.save_view_preferences('T1:B', ViewPreferences(hidden_people=['T1:A']))
    assert (await views.daily(Reader('T1:B')))['summaries'] == []
    with pytest.raises(LookupError):
        await views.daily(Reader('T2:C'), owner='T1:A')
    await archive.set_visibility('T1:A', shared, 'private')
    assert (await views.daily(Reader('T1:B')))['days'] == []


@pytest.mark.asyncio
async def test_live_undated_memory_uses_recorded_time_but_historical_original_does_not(archive, monkeypatch):
    live = await archive.capture('T1:A', WorkInput(source='mcp', source_id='live', content='Live work',
                                                 metadata={'evidence_type': 'agent_memory'}))
    await archive.capture('T1:A', WorkInput(source='codex', source_id='old', content='Undated import', historical=True))
    original = await archive.read(Reader('T1:A'), live['id'])
    views = WorkViews(archive, ViewGateway(), SimpleNamespace(values={}))
    await views.open()
    result = await views.daily(Reader('T1:A'), owner='T1:A')
    assert result['days'] == []  # No partial summaries for today.
    today = datetime.now(UTC).date()
    with pytest.raises(ValueError, match='completed days'):
        await views.daily(Reader('T1:A'), day=today, owner='T1:A')
    with pytest.raises(ValueError, match='completed days'):
        await views.get(Reader('T1:A'), 'T1:A', ViewRequest(kind='daily', day=today))
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr('litellm_memory.work_views.datetime', SimpleNamespace(now=lambda *_: tomorrow))
    result = await views.daily(Reader('T1:A'), owner='T1:A')
    assert result['days'] == [today.isoformat()]
    await views.build(await views.next())
    result = await views.daily(Reader('T1:A'), owner='T1:A')
    assert result['summaries'][0]['view']['source_ids'] == [live['id']]
    assert original['original']['occurred_at'] is None


def test_daily_api_requires_identity_and_never_returns_another_persons_private_day(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.get('/api/v1/daily-summaries').status_code == 401
        enroll(client, app)
        client.post('/api/v1/memories', json={'source_id': 'private', 'content': 'PRIVATE_API_CANARY',
                                           'occurred_at': '2026-09-18T12:00:00Z'})
        own = client.get('/api/v1/daily-summaries?audience=owner&owner=T:OWNER').json()
        assert own['days'] == ['2026-09-18'] and own['summaries'][0]['audience'] == 'owner'
        assert client.get('/api/v1/daily-summaries?day=invalid').status_code == 422
        assert client.get('/api/v1/daily-summaries?day=2999-01-01').status_code == 400
        enroll(client, app, 'OTHER')
        result = client.get('/api/v1/daily-summaries?audience=owner').json()
        assert result['days'] == [] and 'PRIVATE_API_CANARY' not in json.dumps(result)
        assert client.get('/api/v1/daily-summaries?owner=OTHER:WORKSPACE').status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('audience', ['owner', 'public'])
async def test_shared_web_and_slack_agent_reads_the_same_summary_and_checks_scope(archive, monkeypatch, audience):
    from litellm_memory import query

    private = await ready(archive, 'private', 'Private work', '2026-09-18T11:00:00Z')
    shared = await ready(archive, 'public', 'Shared work', '2026-09-18T12:00:00Z', public=True)
    views = archive.views = WorkViews(archive, ViewGateway(), SimpleNamespace(values={}))
    await views.open()
    reader = Reader('T1:A', audience)
    await views.daily(reader, day=date(2026, 9, 18), owner='T1:A')
    await views.build(await views.next())
    expected = await views.daily(reader, day=date(2026, 9, 18), owner='T1:A')

    @asynccontextmanager
    async def chat_client():
        yield object()

    class Stream:
        is_complete = True
        final_output = f'Shared work. [Source](http://localhost:8000/memory/{shared})'

        def __init__(self, agent):
            self.tools = {tool.__name__: tool for tool in agent.tools}
            assert 'start with daily_summaries' in agent.instructions

        async def stream_events(self):
            result = json.loads(await self.tools['daily_summaries']('2026-09-18', 'T1:A'))
            assert result == expected
            ids = result['summaries'][0]['view']['source_ids']
            assert (private in ids) == (audience == 'owner')
            outside = await self.tools['daily_summaries']('2026-09-18', 'T1:B')
            assert 'Outside' in outside
            if False:
                yield None

    gateway = SimpleNamespace(chat_client=chat_client, validate_model=AsyncMock(return_value='chat'))
    monkeypatch.setattr(query, 'function_tool', lambda function: function)
    monkeypatch.setattr(query, 'Agent', lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(query, 'OpenAIChatCompletionsModel', lambda **kwargs: object())
    monkeypatch.setattr(query.Runner, 'run_streamed', lambda agent, *args, **kwargs: Stream(agent))
    assert await query.answer_work(Settings(_env_file=None), archive, gateway, reader,
                                   'Summarize September 18', scope='T1:A') == Stream.final_output


@pytest.mark.asyncio
async def test_failed_daily_job_retries_durably_and_keeps_full_source_input(archive, monkeypatch):
    import asyncio

    await ready(archive, 'retry', 'A useful update', '2026-09-18T12:00:00Z')
    gateway = ViewGateway()
    views = WorkViews(archive, gateway, SimpleNamespace(values={}))
    await views.open()
    generate = gateway.generate
    gateway.generate = AsyncMock(side_effect=OSError('upstream unavailable'))

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr('litellm_memory.work_views.asyncio.sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        await views.worker()
    result = await views.daily(Reader('T1:A'), day=date(2026, 9, 18), owner='T1:A')
    assert result['summaries'][0]['status'] == 'failed'
    assert result['summaries'][0]['view'] is None
    assert await views.next() is None  # Backoff prevents an immediate hot retry loop.
    await archive.db.execute('UPDATE work_views SET next_attempt=0')
    await archive.db.commit()
    gateway.generate = generate
    with pytest.raises(asyncio.CancelledError):
        await views.worker()
    result = await views.daily(Reader('T1:A'), day=date(2026, 9, 18), owner='T1:A')
    assert result['summaries'][0]['status'] == 'ready'
    assert len(gateway.inputs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('output,code', [
    ('{"items": [{"text": "PRIVATE_MODEL_OUTPUT', 'json_invalid'),
    (json.dumps({'items': [{'text': 'PRIVATE_MODEL_OUTPUT' * 100, 'sources': ['private-id']}]}),
     'string_too_long'),
])
async def test_invalid_summary_diagnostics_do_not_log_model_output(archive, caplog, output, code):
    from pydantic import ValidationError

    await ready(archive, 'validation', 'PRIVATE_MEMORY_INPUT', '2026-09-18T12:00:00Z')
    gateway = ViewGateway()
    gateway.generate = AsyncMock(return_value=output)
    views = WorkViews(archive, gateway, SimpleNamespace(values={}))
    await views.open()
    await views.daily(Reader('T1:A'), day=date(2026, 9, 18), owner='T1:A')
    with pytest.raises(ValidationError):
        await views.build(await views.next())
    assert code in caplog.text
    assert 'PRIVATE' not in caplog.text and 'private-id' not in caplog.text and 'T1:A' not in caplog.text
