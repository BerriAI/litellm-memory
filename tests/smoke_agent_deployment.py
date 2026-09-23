"""Exercise the running worker's real SDK under the production container limit.

Run only in disposable Compose CI. The gateway returns synthetic SSE responses;
the deployed HTTP API, queue, LiteAgents, tool execution and streaming are real.
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from litellm_memory.archive import Archive
from litellm_memory.config import Settings
from litellm_memory.crypto import KeyVault
from litellm_memory.setup import DeploymentConfig

OWNER = ':CI_AGENT'
MODEL = 'synthetic-agent'
SENTINEL = 'LITEAGENTS_DEPLOYMENT_OK'
calls = []


class GatewayFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        models = [(MODEL, 'chat'), ('synthetic-embedding', 'embedding')]
        if self.path == '/v1/models':
            self.respond({'data': [{'id': name} for name, _ in models]})
        elif self.path == '/model/info':
            self.respond({'data': [{'model_name': name, 'model_info': {
                'mode': mode, 'supports_function_calling': mode == 'chat'}} for name, mode in models]})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/typesafe/v1/systemone':
            self.respond({'model': 'jev-latest', 'answers': {
                key: {'type': 'noul', 'noul': 1} for key in body['questions']}})
            return
        assert self.path == '/v1/chat/completions', self.path
        assert body['model'] == MODEL and body['stream'] is True
        tools = [message for message in body['messages'] if message['role'] == 'tool']
        calls.append(bool(tools))
        if tools:
            assert OWNER in tools[-1]['content']
            deltas = [{'content': SENTINEL[:10]}, {'content': SENTINEL[10:]}]
            finish = 'stop'
        else:
            assert 'list_people' in [tool['function']['name'] for tool in body['tools']]
            deltas = [{'tool_calls': [{'index': 0, 'id': 'call_people', 'type': 'function',
                                      'function': {'name': 'list_people', 'arguments': '{}'}}]}]
            finish = 'tool_calls'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for delta, reason in [*((delta, None) for delta in deltas), ({}, finish)]:
            chunk = {'id': 'chatcmpl-ci', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                     'model': MODEL, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')


async def seed(settings, vault):
    archive = Archive(settings.state_dir / 'memory.sqlite3', vault, None)
    await archive.open()
    try:
        await archive.enroll(OWNER, {'name': 'Disposable SDK fixture'})
        return (await archive.create_token(OWNER))['token']
    finally:
        await archive.close()


def main():
    limit = Path('/sys/fs/cgroup/memory.max')
    assert limit.read_text().strip() == str(512 * 1024 * 1024), 'Run with a 512 MiB container limit.'
    settings = Settings(_env_file=None)
    vault = KeyVault(settings.state_dir, settings.vault_wrapping_key)
    config = DeploymentConfig(settings, vault)
    assert not config.complete, 'This fixture requires a disposable, incomplete installation.'
    token = asyncio.run(seed(settings, vault))
    server = ThreadingHTTPServer(('127.0.0.1', 0), GatewayFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10) as client:
            client.post('/api/v1/setup/claim', json={'code': config.claim_path.read_text().strip()}).raise_for_status()
            client.put('/api/v1/setup/gateway', json={
                'base_url': f'http://127.0.0.1:{server.server_port}', 'api_key': 'disposable-ci-only',
            }).raise_for_status()
            client.headers['Authorization'] = 'Bearer ' + token
            previous = None
            for _ in range(3):
                response = client.post('/api/v1/chat', json={
                    'question': 'List people, then acknowledge the synthetic deployment check.',
                    'model': MODEL, 'previous_job_id': previous,
                })
                response.raise_for_status()
                previous = response.json()['id']
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    response = client.get('/api/v1/jobs/' + previous)
                    response.raise_for_status()
                    job = response.json()
                    if job['status'] in {'done', 'failed', 'cancelled'}:
                        break
                    time.sleep(0.1)
                assert job['status'] == 'done', job
                assert job['answer'] == SENTINEL, job
            assert calls == [False, True] * 3, calls
            client.get('/health').raise_for_status()
        print(json.dumps({'status': 'passed', 'completed_queries': 3, 'model_requests': len(calls),
                          'container_limit_mib': 512,
                          'container_peak_mib': round(int(Path('/sys/fs/cgroup/memory.peak').read_text()) / 2**20, 1)}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
