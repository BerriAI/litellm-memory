import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import pytest
from liteagents import AssistantMessage, LiteAgentClient, TextBlock, TextDelta
from test_archive import archive as archive_fixture

from litellm_memory.agent_runtime import RunOutput, agent_options, chat_history, function_tool
from litellm_memory.archive import Reader, WorkInput
from litellm_memory.config import Settings
from litellm_memory.gateway import Gateway
from litellm_memory.query import CitationError, answer_work

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


async def test_current_query_uses_real_loop_and_revalidates_evidence(archive):
    record = await archive.capture('T1:A', WorkInput(source_id='synthetic', content='Shipped the migration.'))
    settings = Settings(_env_file=None, public_base_url='http://localhost:8000')
    answer = f'Shipped the migration. [Source]({settings.public_base_url}/memory/{record["id"]})'

    def respond(body, index):
        return 200, (completion(tool='read_work', arguments={'record_id': record['id']}) if index == 1
                     else completion(text=answer))

    with model_gateway(respond) as (url, requests):
        settings.litellm_base_url = url
        settings.litellm_api_key = 'synthetic-key'
        async with Gateway(settings) as gateway:
            gateway.validate_model = AsyncMock(return_value='route')
            assert await answer_work(settings, archive, gateway, Reader('T1:A'), 'What shipped?') == answer
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


async def test_research_agent_with_real_stdio_memory_and_gateway(tmp_path):
    from litellm_memory.agent import answer
    from litellm_memory.memory import memory_call, memory_server

    # Exercise the application's actual node subprocess/session setup with a
    # disposable JSON-RPC server. No GitHub or private archive is accessed.
    script = tmp_path / 'memory.mjs'
    script.write_text('''
import readline from 'node:readline';
for await (const line of readline.createInterface({input: process.stdin})) {
  const request = JSON.parse(line);
  if (request.id === undefined) continue;
  let result;
  if (request.method === 'initialize') {
    result = {protocolVersion: request.params.protocolVersion,
      capabilities: {tools: {}}, serverInfo: {name: 'synthetic-memory', version: '1'}};
  } else if (request.method === 'tools/list') {
    result = {tools: ['memory_search', 'memory_write'].map(name => ({name,
      description: 'Synthetic test tool', inputSchema: {type: 'object',
        properties: {query: {type: 'string'}}, required: ['query']}}))};
  } else if (request.method === 'tools/call') {
    if (request.params.name !== 'memory_search') process.exit(42);
    result = {content: [{type: 'text', text: JSON.stringify({evidence: 'synthetic source'})}]};
  } else { result = {}; }
  process.stdout.write(JSON.stringify({jsonrpc: '2.0', id: request.id, result}) + '\\n');
}
''')

    def respond(body, index):
        names = {tool['function']['name'] for tool in body['tools']}
        assert 'memory_search' in names and 'memory_write' not in names
        if index == 1:
            return 200, completion(tool='memory_search', arguments={'query': 'synthetic'})
        assert 'synthetic source' in body['messages'][-1]['content']
        return 200, completion(text='The synthetic source was found.')

    with model_gateway(respond) as (url, requests):
        settings = Settings(_env_file=None, memory_script=script, state_dir=tmp_path,
                            litellm_base_url=url, litellm_api_key='synthetic-key')
        async with memory_server(settings) as memory:
            assert await memory_call(memory, 'memory_search', {'query': 'q'}) == {'evidence': 'synthetic source'}
            with pytest.raises(ValueError, match='read-only'):
                await memory_call(memory, 'memory_write', {'query': 'forbidden'})
        assert await answer(settings, 'Find synthetic evidence') == 'The synthetic source was found.'
        assert len(requests) == 2


@pytest.mark.parametrize('reason', ['tool_use', 'max_tokens'])
def test_incomplete_run_cannot_be_returned_as_success(reason):
    output = RunOutput()
    with pytest.raises(RuntimeError):
        output.observe(AssistantMessage([TextBlock('partial')], model='test', stop_reason=reason))
        output.final_text()
