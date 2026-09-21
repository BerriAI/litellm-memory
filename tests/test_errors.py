import time

import httpx
from fastapi.testclient import TestClient
from openai import RateLimitError
from test_app import config, signed_event

from litellm_memory.app import create_app
from litellm_memory.errors import explain_error


def rate_limit(message):
    response = httpx.Response(429, request=httpx.Request('POST', 'https://gateway.example/v1/chat/completions'))
    return RateLimitError(message, response=response, body={'error': {'message': message}})


def test_billing_error_is_not_confused_with_temporary_throttling():
    billing = explain_error(rate_limit('OpenAIException - You have no credits remaining. secret-provider-detail'))
    assert not billing.retryable
    assert 'configured model route' in billing.message
    assert 'secret-provider-detail' not in billing.message
    assert explain_error(rate_limit('Rate limit reached for requests per minute')).retryable


def test_slack_billing_failure_is_reported_once_without_retries(tmp_path):
    attempts, replies, reactions = [], [], []

    async def agent(settings, question, history):
        attempts.append(question)
        raise rate_limit('You have no credits remaining.')

    async def api(method, payload):
        if method == 'conversations.replies':
            return {'ok': True, 'messages': []}
        if method in {'chat.postMessage', 'chat.update'}:
            replies.append(payload['text'])
        if method.startswith('reactions.'):
            reactions.append(method)
        return {'ok': True, 'ts': '99'}

    app = create_app(config(tmp_path), agent_fn=agent)
    app.state.slack.api = api
    with TestClient(app) as client:
        signed_event(client, {'type': 'message', 'channel_type': 'im', 'channel': 'D1',
                              'user': 'UOWNER', 'ts': '1', 'text': 'hello'})
        for _ in range(80):
            if 'reactions.remove' in reactions:
                break
            time.sleep(.05)
        assert attempts == ['hello']
        assert sum('reports no API credits' in text for text in replies) == 1
        assert 'reactions.add' in reactions and 'reactions.remove' in reactions
