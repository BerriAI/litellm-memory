import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from test_app import config, signed_event

from moegent.app import create_app
from moegent.progress import report
from moegent.slack import Slack
from moegent.storage import Storage


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
    assert any(method == 'reactions.add' and p['name'] == 'eyes' for method, p in calls)
    assert any(method == 'assistant.threads.setStatus' for method, _ in calls)
    await store.progress(jid, 'Writing an answer', 'Some words')
    slack.last_update.clear()
    await slack.indicator(jid)
    assert any(method == 'chat.update' and 'Some words' in p['text'] for method, p in calls)
    text = 'Uses [one](http://localhost:8000/memory/one) and [two](http://localhost:8000/memory/two).'
    assert await slack.post_answer(job, text)
    final = calls[-1]
    assert final[0] == 'chat.update'
    assert '/sources?ids=one%2Ctwo' in final[1]['text']
    await slack.indicator(jid)
    assert any(method == 'reactions.remove' for method, _ in calls)
    assert calls[-1] == ('assistant.threads.setStatus', {'channel_id': 'D1', 'thread_ts': '1', 'status': ''})
    assert (await store.get_job(jid))['indicator_done']
    assert len([c for c in calls if c[0] == 'chat.postMessage']) == 1
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
    from moegent.slack import formatted
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
