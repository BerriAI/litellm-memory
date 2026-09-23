import asyncio
import re
import time
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import config, signed_event

from litellm_memory.app import create_app
from litellm_memory.progress import report
from litellm_memory.slack import Slack, SlackAPIError
from litellm_memory.storage import Storage


def test_web_stream_cancel_and_idempotent_retry(tmp_path):
    cancelled = []

    async def slow_answer(settings, question, history):
        if question == 'fast':
            return 'The worker is still available.'
        await report('Reading source memories', 'An early fragment')
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with TestClient(create_app(config(tmp_path), agent_fn=slow_answer)) as client:
        payload = {'question': 'slow', 'request_id': str(uuid.uuid4())}
        job_id = client.post('/api/chat', json=payload).json()['id']
        assert client.post('/api/chat', json=payload).json()['id'] == job_id
        for _ in range(30):
            result = client.get('/api/jobs/' + job_id).json()
            if result['partial']:
                break
            time.sleep(.05)
        assert result['phase'] == 'Reading source memories'
        assert result['partial'] == 'An early fragment'
        assert client.post(f'/api/jobs/{job_id}/cancel').json()['status'] == 'cancelled'
        fast = client.post('/api/chat', json={'question': 'fast'}).json()['id']
        for _ in range(30):
            result = client.get('/api/jobs/' + fast).json()
            if result['status'] == 'done':
                break
            time.sleep(.05)
        assert cancelled
        assert result['answer'] == 'The worker is still available.'
        assert client.get('/api/jobs/' + job_id).json()['status'] == 'cancelled'
        client.cookies.clear()
        assert client.post(f'/api/jobs/{job_id}/cancel').status_code == 404


def test_source_shell_and_date_validation(tmp_path):
    with TestClient(create_app(config(tmp_path), start_worker=False)) as client:
        assert 'source-dialog' in client.get('/sources?ids=one,two').text
        assert 'source-dialog' in client.get('/memory/one').text
        assert client.post('/api/memories/search', json={'created_from': '2026-09-20', 'created_before': '2026-09-19'}).status_code == 422


def test_slack_same_message_two_events_and_thread_steering(tmp_path):
    app = create_app(config(tmp_path), start_worker=False)
    with TestClient(app) as client:
        first = {'type': 'app_mention', 'user': 'U1', 'channel': 'C1', 'ts': '10', 'text': '<@UBOT> original'}
        signed_event(client, first, 'event1')
        job = client.portal.call(app.state.store.next_job)
        # message.channels and app_mention can both describe the same mention.
        signed_event(client, {**first, 'type': 'message'}, 'event2')
        assert client.portal.call(app.state.store.next_job) is None
        follow = {'type': 'message', 'user': 'U1', 'channel': 'C1', 'ts': '11', 'thread_ts': '10', 'text': 'Focus on pricing'}
        signed_event(client, follow, 'event3')
        assert client.portal.call(app.state.store.get_job, job['id'])['status'] == 'cancelled'
        steered = client.portal.call(app.state.store.next_job)
        assert steered['payload']['question'] == 'Focus on pricing'
        assert steered['payload']['prior_questions'] == ['original']
        signed_event(client, {**follow, 'user': 'U2', 'ts': '12'}, 'event4')
        assert client.portal.call(app.state.store.get_job, steered['id'])['status'] == 'running'
        signed_event(client, {**follow, 'ts': '13', 'text': 'stop'}, 'event5')
        assert client.portal.call(app.state.store.get_job, steered['id'])['status'] == 'cancelled'
        # Unrelated channel messages never start a conversation.
        signed_event(client, {'type': 'message', 'user': 'U1', 'channel': 'C1', 'ts': '20', 'text': 'normal chat'}, 'event6')
        other = client.portal.call(app.state.store.next_job)
        assert other['payload']['user'] == 'U2'
        assert client.portal.call(app.state.store.next_job) is None


@pytest.mark.asyncio
async def test_slack_feedback_thread_context_and_multiple_sources(tmp_path):
    settings = config(tmp_path)
    store = Storage(tmp_path / 'db')
    await store.open()
    slack = Slack(settings, store)
    calls = []

    async def api(method, payload):
        calls.append((method, payload))
        if method == 'chat.postMessage':
            return {'ok': True, 'ts': '99'}
        if method == 'conversations.replies':
            if not payload.get('cursor'):
                return {'ok': True, 'messages': [{'ts': '1', 'user': 'U2', 'text': 'Earlier human context'}],
                        'response_metadata': {'next_cursor': 'next'}}
            return {'ok': True, 'messages': [
                {'ts': '2', 'user': 'UBOT', 'text': 'Earlier answer'},
                {'ts': '3', 'user': 'UBOT', 'text': 'I’ll look into that and reply here when the answer is ready.'},
                {'ts': '99', 'user': 'UBOT', 'text': '_Working…_'},
                {'ts': '100', 'user': 'U1', 'text': 'question'},
            ]}
        return {'ok': True}

    slack.api = api
    p = {'team': 'T1', 'channel': 'D1', 'user': 'U1', 'thread_ts': '1', 'message_ts': '100', 'question': 'question'}
    jid = await store.enqueue('T1:U1', 'slack', p)
    await slack.indicator(jid)
    job = await store.next_job()
    history = await slack.history(job)
    assert history == [{'role': 'user', 'content': '<@U2>: Earlier human context'}, {'role': 'assistant', 'content': 'Earlier answer'}]
    assert any(method == 'chat.postMessage' and 'reply here when the answer is ready' in p['text'] for method, p in calls)
    assert not any(method == 'assistant.threads.setStatus' for method, _ in calls)
    await store.progress(jid, 'Writing an answer', 'Some words')
    await slack.indicator(jid)
    assert not any(method == 'chat.update' for method, p in calls)
    text = 'Uses [one](http://localhost:8000/memory/one) and [two](http://localhost:8000/memory/two).'
    assert await slack.post_answer(job, text)
    final = calls[-1]
    assert final[0] == 'chat.postMessage'
    assert '/sources?ids=one%2Ctwo' in final[1]['text']
    await slack.indicator(jid)
    assert calls[-1] == final
    assert not any(method.startswith('reactions.') for method, _ in calls)
    assert (await store.get_job(jid))['indicator_done']
    assert len([c for c in calls if c[0] == 'chat.postMessage']) == 2
    await store.close()


@pytest.mark.asyncio
async def test_cancelled_slack_never_posts_stale_answer_and_falls_back_honestly(tmp_path):
    store = Storage(tmp_path / 'db')
    await store.open()
    slack = Slack(config(tmp_path), store)
    slack.api = AsyncMock(side_effect=RuntimeError('Slack API: missing_scope'))
    p = {'team': 'T1', 'channel': 'D1', 'user': 'U1', 'thread_ts': '1', 'message_ts': '2',
         'question': 'updated', 'prior_questions': ['original']}
    jid = await store.enqueue('T1:U1', 'slack', p)
    job = await store.next_job()
    history = await slack.history(job)
    assert 'retrieval is unavailable' in history[0]['content']
    assert history[-1]['content'] == 'original'
    await store.cancel(jid)
    slack.api.reset_mock()
    assert not await slack.post_answer(job, 'obsolete answer')
    slack.api.assert_not_called()
    await store.result(jid, answer='late', status='done')
    assert (await store.get_job(jid))['status'] == 'cancelled'
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', ['A' * 4500, ('A' * 1700 + '\n') * 8,
                                'A' * 10980 + '[source](http://localhost:8000/memory/one)'])
async def test_long_slack_answer_posts_followups_and_preserves_all_text_and_sources(tmp_path, body):
    settings = config(tmp_path)
    store = Storage(tmp_path / 'db')
    await store.open()
    slack = Slack(settings, store)
    calls = []

    async def api(method, payload):
        if method == 'chat.update' and len(payload['text']) > 4000:
            raise SlackAPIError('msg_too_long')
        assert len(payload['text']) <= 40000
        assert len(payload['blocks'][0]['text']) <= 12000
        calls.append((method, payload))
        return {'ok': True, 'ts': str(len(calls))}

    slack.api = api
    p = {'team': 'T1', 'channel': 'D1', 'user': 'U1', 'thread_ts': '1', 'question': 'question'}
    jid = await store.enqueue('T1:U1', 'slack', p)
    await slack.indicator(jid)
    job = await store.next_job()
    text = body + '\n[one](http://localhost:8000/memory/one) [two](http://localhost:8000/memory/two)'
    try:
        assert await slack.post_answer(job, text)
        delivery = calls[1:]
        assert all(method == 'chat.postMessage' and payload['thread_ts'] == '1'
                   for method, payload in delivery)
        complete = ''.join(payload['blocks'][0]['text'] for _, payload in delivery)
        assert complete.startswith(text)
        assert complete.endswith('/sources?ids=one%2Ctwo)')
        links = r'\[[^\]\n]*\]\(https?://[^\s)]+\)'
        assert [link for _, payload in delivery for link in re.findall(links, payload['blocks'][0]['text'])] == re.findall(links, complete)
        assert (await store.get_job(jid))['status'] == 'done'
        assert (await store.get_job(jid))['answer'] == complete
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code,expected', [('msg_too_long', 'msg_too_long'),
                                          ('private message content', 'unknown_error')])
async def test_slack_failure_logs_only_method_and_sanitized_code(tmp_path, monkeypatch, caplog, code, expected):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'ok': False, 'error': code, 'response_metadata': {'messages': ['private upstream body']}}))
    monkeypatch.setattr('litellm_memory.slack.httpx.AsyncClient',
                        lambda **kwargs: real_client(transport=transport, **kwargs))
    slack = Slack(config(tmp_path), None)
    slack.token = AsyncMock(return_value='synthetic-secret')
    with pytest.raises(SlackAPIError):
        await slack.api('chat.update', {'text': 'private answer', 'channel': 'C1', 'ts': '1'})
    assert f'Slack chat.update failed ({expected})' in caplog.text
    assert 'private' not in caplog.text and 'synthetic-secret' not in caplog.text


def test_slack_followup_cancels_running_model_and_answers_latest(tmp_path):
    starts, cancelled, delivered = [], [], []

    async def agent(settings, question, history):
        starts.append(question)
        if question == 'original':
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append(question)
                raise
        return 'Answer to ' + question

    app = create_app(config(tmp_path), agent_fn=agent)

    async def api(method, payload):
        if method == 'conversations.replies':
            return {'ok': True, 'messages': []}
        if method in {'chat.postMessage', 'chat.update'}:
            delivered.append(payload['text'])
        return {'ok': True, 'ts': '9'}

    app.state.slack.api = api
    with TestClient(app) as client:
        signed_event(client, {'type': 'message', 'channel_type': 'im', 'user': 'U1', 'channel': 'D1', 'ts': '1', 'text': 'original'}, 'e1')
        for _ in range(30):
            if starts:
                break
            time.sleep(.05)
        signed_event(client, {'type': 'message', 'channel_type': 'im', 'user': 'U1', 'channel': 'D1', 'thread_ts': '1', 'ts': '2', 'text': 'new direction'}, 'e2')
        for _ in range(40):
            if 'Answer to new direction' in delivered:
                break
            time.sleep(.05)
        assert cancelled == ['original']
        assert 'Answer to original' not in delivered
        assert 'Answer to new direction' in delivered


@pytest.mark.asyncio
async def test_slack_native_markdown_and_workspace_fallback(tmp_path):
    from litellm_memory.slack import formatted
    markdown = '# Heading\n\n**Bold** and [source](https://example.com).\n\n- First\n- Second\n\n```python\nprint("**literal**")\n```'
    payload = formatted(markdown)
    assert payload['blocks'] == [{'type': 'markdown', 'text': markdown}]
    assert '*Bold*' in payload['text'] and '**Bold**' not in payload['text']
    assert 'print("**literal**")' in payload['text']
    slack = Slack(config(tmp_path), None)
    slack.api = AsyncMock(side_effect=[RuntimeError('Slack API: invalid_blocks'), {'ok': True}])
    assert await slack.message_api('chat.update', {'channel': 'D1', 'ts': '1', **payload}) == {'ok': True}
    assert slack.api.call_args.args[1]['blocks'] == []
    assert slack.api.call_args.args[1]['text'] == payload['text']


def test_owner_created_group_dm_acknowledges_before_model_then_delivers_once(tmp_path):
    calls = []

    async def agent(settings, question, history):
        # Exercise the real queue + worker + monitor, rather than calling the indicator directly.
        assert any(method == 'chat.postMessage' for method, _ in calls)
        await asyncio.sleep(.1)
        return 'Group DM answer'

    app = create_app(config(tmp_path, access_mode='allowlist', allowed_slack_users='T1:UTIN'), agent_fn=agent)

    async def api(method, payload):
        calls.append((method, payload))
        if method == 'conversations.info':
            return {'ok': True, 'channel': {'is_mpim': True, 'is_member': True, 'creator': 'UOWNER', 'created': 1}}
        if method == 'conversations.replies':
            return {'ok': True, 'messages': []}
        return {'ok': True, 'ts': '99'}

    app.state.slack.api = api
    with TestClient(app) as client:
        signed_event(client, {'type': 'app_mention', 'user': 'UTIN', 'channel': 'C-GROUP-DM',
                              'channel_type': 'mpim', 'ts': '10', 'text': '<@UBOT> hello'})
        for _ in range(80):
            if any(method == 'chat.postMessage' and payload['text'] == 'Group DM answer' for method, payload in calls):
                break
            time.sleep(.05)
        assert client.portal.call(app.state.store.channel_allowed, 'T1', 'C-GROUP-DM')
        assert any(method == 'chat.postMessage' and payload['text'] == 'Group DM answer' for method, payload in calls)
        reactions = [(method, payload) for method, payload in calls if method.startswith('reactions.')]
        assert reactions == []
        assert not any(method == 'assistant.threads.setStatus' for method, _ in calls)


@pytest.mark.asyncio
async def test_group_dm_recovery_never_bypasses_owner_or_revocation(tmp_path):
    store = Storage(tmp_path / 'db')
    await store.open()
    slack = Slack(config(tmp_path, access_mode='allowlist', allowed_slack_users='T1:UOWNER,T1:UTIN'), store)
    p = {'team': 'T1', 'channel': 'C1', 'user': 'UTIN'}
    for info in [
        {'is_mpim': True, 'is_member': True, 'creator': 'UTIN', 'created': 1},
        {'is_mpim': False, 'is_member': True, 'creator': 'UOWNER', 'created': 1},
        {'is_mpim': True, 'is_member': False, 'creator': 'UOWNER', 'created': 1},
    ]:
        slack.conversation_cache.clear()
        slack.api = AsyncMock(return_value={'channel': info})
        assert not await slack.permitted(p)
        assert await store.channel_approval('T1', 'C1') is None
    slack.conversation_cache.clear()
    slack.api = AsyncMock(side_effect=SlackAPIError('missing_scope'))
    assert not await slack.permitted(p)
    assert not await slack.permitted(p)
    assert slack.api.await_count == 1  # Avoid hammering Slack while an install is being updated.
    await store.channel_event('T1', 'C1', False, 2)
    slack.api.reset_mock()
    assert not await slack.permitted(p)
    assert not await slack.permitted({**p, 'user': 'UOUTSIDER'})
    slack.api.assert_not_called()
    await store.close()


@pytest.mark.asyncio
async def test_acknowledgment_retries_without_streaming_or_overwriting_final(tmp_path):
    store = Storage(tmp_path / 'db')
    await store.open()
    slack = Slack(config(tmp_path), store)
    slack.api = AsyncMock(side_effect=[RuntimeError('temporarily unavailable'), {'ok': True, 'ts': '99'}, {'ok': True}])
    p = {'team': 'T1', 'channel': 'D1', 'user': 'UOWNER', 'thread_ts': '1', 'message_ts': '2'}
    jid = await store.enqueue('owner', 'slack', p)
    await slack.indicator(jid)
    assert not (await store.get_job(jid))['response_ts']
    await slack.indicator(jid)
    assert (await store.get_job(jid))['response_ts'] == '99'
    job = await store.next_job()
    await store.progress(jid, 'Some stage', 'Unverified text')
    await slack.indicator(jid)
    assert slack.api.await_count == 2
    assert await slack.post_answer(job, 'Verified answer')
    await slack.indicator(jid)
    assert slack.api.await_count == 3
    assert (await store.get_job(jid))['indicator_done']
    assert slack.api.call_args.args[0] == 'chat.postMessage'
    assert slack.api.call_args.args[1]['text'] == 'Verified answer'
    await store.close()
