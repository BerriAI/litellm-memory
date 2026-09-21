import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_archive import archive as archive_fixture
from test_model_settings import application, enroll
from test_work_views import ViewGateway, ready

from litellm_memory.archive import Reader, WorkInput
from litellm_memory.config import Settings
from litellm_memory.view_preferences import ViewPreferences
from litellm_memory.work_views import ViewRequest, WorkViews

archive = archive_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('preferences', [
    {'hidden_people': ['T1:A']}, {'hidden_projects': [' Project Secret ']},
    {'hidden_sources': ['Claude Code']}, {'hidden_types': ['Decision']},
])
async def test_saved_filters_narrow_retrieval_but_never_delete_or_change_sharing(archive, preferences):
    saved = await archive.capture('T1:A', WorkInput(source_id='one', source='claude_code',
        content='PRIVATE_ORIGINAL', metadata={'project': 'Project Secret'}))
    await archive.set_visibility('T1:A', saved['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(summary='Approved shared summary', kind='progress', labels={'decision': .9})
    await archive.complete_work(row, payload, private_match=False)
    assert (await archive.search(Reader('T1:A')))['total'] == 1
    original = await archive.row(saved['id'])
    before_policy = await archive.policy_stamp(Reader('T1:A'))
    await archive.save_view_preferences('T1:A', ViewPreferences(**preferences))
    assert before_policy != await archive.policy_stamp(Reader('T1:A'))
    found = await archive.search(Reader('T1:A'))
    assert found['results'] == [] and found['total'] == 0 and found['personal_filters_active']
    assert (await archive.read(Reader('T1:A'), saved['id']))['content'] == 'PRIVATE_ORIGINAL'
    assert len(await archive.records(Reader('T1:A'))) == 1  # Export path retains hidden work.
    assert await archive.row(saved['id']) == original  # No new processing/classification or publication mutation.
    assert (await archive.search(Reader('T1:B')))['total'] == 1  # Personal choice is not a sharing rule.
    assert (await archive.search(Reader('T2:C')))['total'] == 0
    await archive.save_view_preferences('T1:A', ViewPreferences())
    assert (await archive.search(Reader('T1:A')))['total'] == 1


@pytest.mark.asyncio
async def test_project_filters_use_only_permitted_metadata_and_preferences_survive_restart(archive):
    saved = await archive.capture('T1:A', WorkInput(source_id='project', content='Work',
        metadata={'project': 'PRIVATE_PROJECT_CANARY'}))
    await archive.set_visibility('T1:A', saved['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(summary='Safe summary', projects=['Approved project'], labels={'progress': 1})
    await archive.complete_work(row, payload, private_match=False)
    assert (await archive.search(Reader('T1:A'), project='private_project_canary'))['total'] == 1
    assert (await archive.search(Reader('T1:B'), project='private_project_canary'))['total'] == 0
    assert (await archive.search(Reader('T1:B'), project='approved PROJECT'))['total'] == 1
    prefs = ViewPreferences(hidden_projects=['PRIVATE_FILTER_CANARY'])
    await archive.save_view_preferences('T1:A', prefs)
    stamp = await archive.policy_stamp(Reader('T1:A'))
    await archive.save_view_preferences('T1:A', prefs)
    assert stamp == await archive.policy_stamp(Reader('T1:A'))  # Exact saves do not invalidate work.
    assert 'PRIVATE_FILTER_CANARY' not in json.dumps(await archive.people(Reader('T1:B')))
    await archive.close()
    await archive.open()
    assert await archive.view_preferences('T1:A') == prefs
    assert (await archive.view_preferences('T1:B')).active is False
    await archive.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert b'private_filter_canary' not in archive.path.read_bytes()


@pytest.mark.asyncio
async def test_personal_catchups_have_separate_filtered_evidence_and_do_not_poison_shared_views(archive):
    source = await ready(archive, 'public', 'Approved work', '2026-09-19T12:00:00Z', public=True)
    await ready(archive, 'private', 'PRIVATE_CANARY', '2026-09-19T13:00:00Z')
    gateway = ViewGateway()
    views = WorkViews(archive, gateway, SimpleNamespace(values={}))
    await views.open()
    normal = await views.get(Reader('T1:B'), 'T1:A', ViewRequest())
    await views.build(await views.next())
    await archive.save_view_preferences('T1:B', ViewPreferences(hidden_projects=['Project A']))
    filtered = await views.get(Reader('T1:B'), 'T1:A', ViewRequest())
    assert filtered['id'] != normal['id'] and filtered['view'] is None
    await views.build(await views.next())
    result = await views.get(Reader('T1:B'), 'T1:A', ViewRequest())
    assert result['view']['source_ids'] == [] and result['view']['coverage']['total'] == 0
    assert 'PRIVATE_CANARY' not in json.dumps(gateway.inputs)
    unchanged = await views.get(Reader('T1:A', 'public'), 'T1:A', ViewRequest())
    assert unchanged['id'] == normal['id']
    assert unchanged['view']['source_ids'] == [source]
    await archive.save_view_preferences('T1:B', ViewPreferences(hidden_people=['T1:A']))
    with pytest.raises(ValueError, match='excluded'):
        await views.get(Reader('T1:B'), 'T1:A', ViewRequest())


def test_view_preferences_api_is_owner_scoped_and_invalidates_old_chat(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        token = client.portal.call(app.state.archive.create_token, 'T:OWNER')['token']
        first = client.post('/api/chat', json={'question': 'What happened?'}).json()
        assert client.get('/api/jobs/' + first['id']).status_code == 200
        payload = {'hidden_types': ['decision']}
        assert client.put('/api/view-preferences', json=payload,
                          headers={'Authorization': 'Bearer ' + token}).status_code == 403
        assert client.put('/api/view-preferences', json=payload, headers={'Origin': 'https://attacker.example'}).status_code == 403
        assert client.put('/api/view-preferences', json=payload).status_code == 200
        assert client.get('/api/view-preferences').json()['hidden_types'] == ['decision']
        assert client.get('/api/jobs/' + first['id']).status_code == 409
        assert client.get('/api/view-preferences', headers={'Authorization': 'Bearer ' + token}).json()['hidden_types'] == ['decision']


def test_preference_input_is_bounded_and_type_filters_use_cached_classification():
    with pytest.raises(ValidationError):
        ViewPreferences(hidden_projects=['x' * 201])
    with pytest.raises(ValidationError):
        ViewPreferences(hidden_types=['progress'] * 101)
    prefs = ViewPreferences(hidden_types=['DECISION', ' decision '], hidden_sources=['claude_code', 'Claude Code'])
    assert prefs.hidden_types == ['decision'] and prefs.hidden_sources == ['claude']
    assert prefs.includes({'kind': 'work', 'labels': {'decision': .79}})
    assert not prefs.includes({'kind': 'work', 'labels': {'decision': .8}})
    assert not prefs.includes({'kind': 'decision', 'labels': {}})
    assert not prefs.includes({'source': 'claude_code'})
    # Once Jev has classified the source, the summarizer's suggested type must
    # not overrule a negative Jev result.
    assert prefs.includes({'kind': 'decision', 'labels': {'decision': .1}})


@pytest.mark.asyncio
async def test_type_search_uses_jev_labels_instead_of_a_conflicting_summary_type(archive):
    saved = await archive.capture('T1:A', WorkInput(source_id='type', content='A plan'))
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(kind='decision', summary='A planned action', labels={'decision': .1, 'plan': .99})
    await archive.complete_work(row, payload, private_match=False)
    assert (await archive.search(Reader('T1:A'), kind='decision'))['total'] == 0
    assert (await archive.search(Reader('T1:A'), kind='plan'))['results'][0]['id'] == saved['id']


@pytest.mark.asyncio
async def test_agent_read_tool_cannot_bypass_a_personal_filter(archive, monkeypatch):
    from litellm_memory import query
    saved = await archive.capture('T1:A', WorkInput(source_id='hidden', source='codex', content='EXCLUDED_CANARY'))
    await archive.save_view_preferences('T1:A', ViewPreferences(hidden_sources=['codex']))

    @asynccontextmanager
    async def chat_client():
        yield object()

    class Stream:
        is_complete = True
        final_output = 'No matching work.'

        def __init__(self, agent):
            self.tools = {tool.__name__: tool for tool in agent.tools}

        async def stream_events(self):
            result = json.loads(await self.tools['search_work']('EXCLUDED_CANARY'))
            assert result['total'] == 0
            read = await self.tools['read_work'](saved['id'])
            assert 'EXCLUDED_CANARY' not in read and 'preferences' in read
            if False:
                yield None

    gateway = SimpleNamespace(chat_client=chat_client, validate_model=AsyncMock(return_value='chat'),
                              embeddings=AsyncMock(return_value=[[]]))
    monkeypatch.setattr(query, 'function_tool', lambda function: function)
    monkeypatch.setattr(query, 'Agent', lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(query, 'OpenAIChatCompletionsModel', lambda **kwargs: object())
    monkeypatch.setattr(query.Runner, 'run_streamed', lambda agent, *args, **kwargs: Stream(agent))
    assert await query.answer_work(Settings(_env_file=None), archive, gateway, Reader('T1:A'), 'My work') == 'No matching work.'


@pytest.mark.asyncio
async def test_public_search_does_not_expose_private_archive_counts_or_filter_state(archive):
    await archive.capture('T1:A', WorkInput(source_id='only-private', content='PRIVATE_COUNT_CANARY'))
    await archive.save_view_preferences('T1:A', ViewPreferences(hidden_types=['decision']))
    private = await archive.search(Reader('T1:A'))
    assert private['coverage']['total'] == 1 and private['personal_filters_active']
    shared = await archive.search(Reader('T1:A', 'public'))
    assert shared['total'] == 0 and shared['results'] == []
    assert shared['coverage'] is None and not shared['personal_filters_active']
