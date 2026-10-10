import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from liteagents import AssistantMessage, LiteAgentClient, TextBlock, TextDelta
from test_archive import archive as archive_fixture

from litellm_memory.agent_runtime import RunOutput, agent_options, chat_history, function_tool
from litellm_memory.archive import Reader, WorkInput
from litellm_memory.config import Settings
from litellm_memory.gateway import Gateway
from litellm_memory.observability import request_source
from litellm_memory.query import CitationError, answer_work
from litellm_memory.work_views import WorkViews

archive = archive_fixture


@contextmanager
def model_gateway(responder):
    """Exercise LiteLLM's actual OpenAI bridge over HTTP, without model mocks."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, dict(self.headers), body))
            status, content = responder(body, len(requests))
            self.send_response(status)
            self.send_header('Content-Type', 'text/event-stream' if status == 200 else 'application/json')
            self.end_headers()
            if status != 200:
                self.wfile.write(json.dumps(content).encode())
                return
            for chunk in content:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def completion(*, text=None, tool=None, arguments=None):
    delta = {'content': text} if tool is None else {'tool_calls': [{
        'index': 0, 'id': 'call_1', 'type': 'function',
        'function': {'name': tool, 'arguments': json.dumps(arguments or {})},
    }]}
    base = {'id': 'synthetic', 'model': 'gateway-route', 'object': 'chat.completion.chunk', 'created': 1}
    return [
        {**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', **delta}, 'finish_reason': None}]},
        {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if tool else 'stop'}]},
        {**base, 'choices': [], 'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}},
    ]


async def test_real_gateway_streaming_tool_roundtrip_history_privacy_and_usage():
    calls = []

    @function_tool
    async def lookup(query: str, person: str | None = None) -> str:
        calls.append((query, person))
        return 'synthetic evidence'

    def respond(body, index):
        if index == 1:
            return 200, completion(tool='lookup', arguments={'query': 'hello'})
        assert body['messages'][-1]['role'] == 'tool'
        assert 'synthetic evidence' in body['messages'][-1]['content']
        return 200, completion(text='Grounded answer')

    with model_gateway(respond) as (url, requests):
        settings = Settings(_env_file=None, litellm_base_url=url, litellm_api_key='synthetic-key')
        options = agent_options(settings, 'team/model', tools=[lookup])
        history = chat_history([{'role': 'user', 'content': 'earlier question'},
                                {'role': 'assistant', 'content': 'earlier answer'}])
        output, deltas = RunOutput(), []
        async with LiteAgentClient(options=options, history=history) as agent:
            async for event in agent.query('followup'):
                if isinstance(event, TextDelta):
                    deltas.append(event.text)
                elif isinstance(event, AssistantMessage):
                    output.observe(event)
        assert output.final_text() == ''.join(deltas) == 'Grounded answer'
        assert output.requests == 2 and output.input_tokens == 24 and output.output_tokens == 16
        assert calls == [('hello', None)]
        assert requests[0][2]['messages'][0]['content'] == 'earlier question'
        for path, headers, body in requests:
            assert path == '/v1/chat/completions'
            assert body['model'] == 'team/model' and body['no-log'] is True and body['stream'] is True
            lowered = {key.lower(): value for key, value in headers.items()}
            assert lowered['authorization'] == 'Bearer synthetic-key'
            assert lowered['x-litellm-enable-message-redaction'] == 'true'


async def test_real_gateway_auth_error_is_not_retried():
    from litellm import AuthenticationError

    with model_gateway(lambda *_: (401, {'error': {'message': 'synthetic bad key'}})) as (url, requests):
        settings = Settings(_env_file=None, litellm_base_url=url, litellm_api_key='synthetic-key')
        async with LiteAgentClient(options=agent_options(settings, 'route')) as agent:
            with pytest.raises(AuthenticationError):
                _ = [event async for event in agent.query('hello')]
        assert len(requests) == 1


@pytest.mark.parametrize('surface', ['web', 'slack'])
async def test_current_query_uses_real_loop_and_revalidates_evidence(archive, surface):
    record = await archive.capture('T1:A', WorkInput(source_id='synthetic', content='Shipped the migration.'))
    settings = Settings(_env_file=None, public_base_url='http://localhost:8000')
    answer = f'Shipped the migration. [Source]({settings.public_base_url}/memory/{record["id"]})'

    def respond(body, index):
        system = next(message['content'] for message in body['messages'] if message['role'] == 'system')
        assert ('This response will be delivered in a Slack thread.' in system) == (surface == 'slack')
        return 200, (completion(tool='read_work', arguments={'record_id': record['id']}) if index == 1
                     else completion(text=answer))

    with model_gateway(respond) as (url, requests):
        settings.litellm_base_url = url
        settings.litellm_api_key = 'synthetic-key'
        async with Gateway(settings) as gateway:
            gateway.validate_model = AsyncMock(return_value='route')
            context = request_source.set(surface)
            try:
                assert await answer_work(settings, archive, gateway, Reader('T1:A'), 'What shipped?') == answer
            finally:
                request_source.reset(context)
        assert len(requests) == 2


async def test_current_query_stops_before_delivering_after_access_revoked(archive):
    settings = Settings(_env_file=None)
    checks = 0

    async def policy_stamp(reader):
        nonlocal checks
        checks += 1
        return 'initial' if checks == 1 else 'revoked'

    archive.policy_stamp = policy_stamp
    with model_gateway(lambda *_: (200, completion(text='Must never deliver'))) as (url, _):
        settings.litellm_base_url, settings.litellm_api_key = url, 'synthetic-key'
        async with Gateway(settings) as gateway:
            gateway.validate_model = AsyncMock(return_value='route')
            with pytest.raises(PermissionError, match='Access changed'):
                await answer_work(settings, archive, gateway, Reader('T1:A'), 'What shipped?')


async def test_slack_today_search_pages_live_public_work_in_workspace_timezone(archive, monkeypatch):
    monkeypatch.setattr('litellm_memory.archive.now', lambda: '2026-09-23T01:40:00+00:00')
    records = set()
    for index in range(31):
        saved = await archive.capture('T1:A', WorkInput(source='codex', source_id=f'live-{index}',
                                                       content='Live work update'))
        records.add(saved['id'])
        await archive.set_visibility('T1:A', saved['id'], 'public')
        row = await archive.next_work()
        payload = await archive.decode(row)
        payload.update(summary='Approved live update', labels={'progress': .95})
        await archive.complete_work(row, payload, private_match=False)
    await archive.capture('T1:A', WorkInput(source='codex', source_id='private', content='PRIVATE_TODAY_CANARY'))
    settings = Settings(_env_file=None, public_base_url='http://localhost:8000')
    seen, cited = set(), []
    arguments = {'query': '', 'person': 'T1:A', 'from_date': '2026-09-22', 'before_date': '2026-09-23'}

    def respond(body, index):
        assert 'PRIVATE_TODAY_CANARY' not in json.dumps(body)
        if index == 1:
            system = body['messages'][0]['content']
            assert 'Workspace timezone: America/Los_Angeles' in system
            assert 'This response will be delivered in a Slack thread.' in system
            return 200, completion(tool='search_work', arguments=arguments)
        if index in {2, 3}:
            page = json.loads(body['messages'][-1]['content'])
            assert page['total'] == 31
            assert page['next_offset'] == (30 if index == 2 else None)
            assert len(page['results']) == (30 if index == 2 else 1)
            assert not seen.intersection(item['id'] for item in page['results'])
            seen.update(item['id'] for item in page['results'])
            assert all(item['date_basis'] == 'recorded' for item in page['results'])
            if index == 2:
                return 200, completion(tool='search_work', arguments={**arguments, 'offset': 30})
            assert seen == records
            cited.append(page['results'][0]['id'])
            return 200, completion(tool='read_work', arguments={'record_id': cited[0]})
        return 200, completion(text=f'Today’s update. [Source]({settings.public_base_url}/memory/{cited[0]})')

    with model_gateway(respond) as (url, requests):
        settings.litellm_base_url, settings.litellm_api_key = url, 'synthetic-key'
        async with Gateway(settings) as gateway:
            gateway.validate_model = AsyncMock(return_value='route')
            gateway.embeddings = AsyncMock(side_effect=AssertionError('Empty browsing needs no embedding'))
            archive.views = WorkViews(archive, gateway, SimpleNamespace(values={'timezone': 'America/Los_Angeles'}))
            context = request_source.set('slack')
            try:
                answer = await answer_work(settings, archive, gateway, Reader('T1:B', 'public'), 'What did Alice do today?')
            finally:
                request_source.reset(context)
            assert cited[0] in answer
            gateway.embeddings.assert_not_called()
        assert len(requests) == 4


@pytest.mark.parametrize('citation', ['returned', 'invented'])
async def test_search_only_draft_is_grounded_in_full_public_sources_before_delivery(archive, citation):
    saved = await archive.capture('T1:A', WorkInput(source='codex', source_id='search-only',
        content='PRIVATE_ORIGINAL_CANARY'))
    await archive.set_visibility('T1:A', saved['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    summary = 'A' * 9000 + ' The full evidence says the change remains unmerged.'
    payload.update(summary=summary, labels={'progress': .95})
    await archive.complete_work(row, payload, private_match=False)
    await archive.capture('T1:A', WorkInput(source='codex', source_id='private-grounding',
                                           content='PRIVATE_RECORD_CANARY'))
    settings = Settings(_env_file=None, public_base_url='http://localhost:8000')
    record_id = saved['id'] if citation == 'returned' else 'invented'
    draft = f'Shipped. [Source]({settings.public_base_url}/memory/{record_id})'
    corrected = f'The change remains unmerged. [Source]({settings.public_base_url}/memory/{saved["id"]})'

    def respond(body, index):
        return 200, (completion(tool='search_work', arguments={'query': '', 'person': 'T1:A'})
                     if index == 1 else completion(text=draft))

    async def ground(messages, **kwargs):
        evidence = json.loads(messages[-1]['content'])
        assert evidence['question'] == 'Did it ship?'
        assert evidence['answer'] == draft.replace(settings.public_base_url + '/memory/' + saved['id'], 'memory-source:S1')
        assert len(evidence['evidence']) == 1
        assert evidence['evidence'][0]['text'] == summary  # Includes the late correction past 8,000 chars.
        assert evidence['evidence'][0]['date_basis'] == 'recorded'
        assert 'PRIVATE_ORIGINAL_CANARY' not in json.dumps(messages)
        assert 'PRIVATE_RECORD_CANARY' not in json.dumps(messages)
        return 'The change remains unmerged. [Source](memory-source:S1)'

    with model_gateway(respond) as (url, requests):
        settings.litellm_base_url, settings.litellm_api_key = url, 'synthetic-key'
        async with Gateway(settings) as gateway:
            gateway.validate_model = AsyncMock(return_value='route')
            gateway.generate = AsyncMock(side_effect=ground)
            assert await answer_work(settings, archive, gateway, Reader('T1:B', 'public'), 'Did it ship?') == corrected
            gateway.generate.assert_awaited_once()
        assert len(requests) == 2


@pytest.mark.parametrize('role', ['assistant', 'user'])
async def test_followup_revalidates_prior_answer_citations_but_not_user_supplied_links(archive, role):
    record = await archive.capture('T1:A', WorkInput(source_id='followup', content='Shipped the migration.'))
    settings = Settings(_env_file=None, public_base_url='http://localhost:8000')
    answer = f'Shipped the migration. [Source]({settings.public_base_url}/memory/{record["id"]})'
    history = [{'role': role, 'content': answer}]
    with model_gateway(lambda *_: (200, completion(text=answer))) as (url, requests):
        settings.litellm_base_url, settings.litellm_api_key = url, 'synthetic-key'
        async with Gateway(settings) as gateway:
            gateway.validate_model = AsyncMock(return_value='route')
            gateway.generate = AsyncMock(return_value=answer)
            if role == 'assistant':
                assert await answer_work(settings, archive, gateway, Reader('T1:A'), 'Which migration?', history) == answer
                gateway.generate.assert_not_called()
            else:
                with pytest.raises(CitationError):
                    await answer_work(settings, archive, gateway, Reader('T1:A'), 'Which migration?', history)
            assert len(requests) == 1


@pytest.mark.parametrize('audience,scope,error', [('public', None, LookupError), ('owner', 'T1:B', PermissionError)])
async def test_prior_citations_cannot_expand_current_audience_or_person_scope(archive, audience, scope, error):
    record = await archive.capture('T1:A', WorkInput(source_id='private-followup', content='Private evidence'))
    settings = Settings(_env_file=None, public_base_url='http://localhost:8000')
    answer = f'[Source]({settings.public_base_url}/memory/{record["id"]})'
    with model_gateway(lambda *_: (200, completion(text=answer))) as (url, _):
        settings.litellm_base_url, settings.litellm_api_key = url, 'synthetic-key'
        async with Gateway(settings) as gateway:
            gateway.validate_model = AsyncMock(return_value='route')
            with pytest.raises(error):
                await answer_work(settings, archive, gateway, Reader('T1:A', audience), 'Follow up',
                                  [{'role': 'assistant', 'content': answer}], scope=scope)


async def test_tool_argument_validation_rejects_unknown_fields_and_keeps_defaults():
    seen = []

    @function_tool
    async def lookup(query: str, person: str | None = None):
        seen.append((query, person))
        return 'ok'

    assert await lookup.execute({'query': 'q'}) == 'ok'
    with pytest.raises(ValueError):
        await lookup.execute({'query': 'q', 'unauthorized': True})
    assert seen == [('q', None)]




@pytest.mark.parametrize('reason', ['tool_use', 'max_tokens'])
def test_incomplete_run_cannot_be_returned_as_success(reason):
    output = RunOutput()
    with pytest.raises(RuntimeError):
        output.observe(AssistantMessage([TextBlock('partial')], model='test', stop_reason=reason))
        output.final_text()
