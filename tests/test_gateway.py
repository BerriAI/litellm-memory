import asyncio

import httpx
import pytest

from litellm_memory.config import Settings
from litellm_memory.gateway import Gateway, GatewayError


@pytest.mark.asyncio
async def test_catalog_obeys_key_permissions_and_never_exposes_provider_credentials():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": n} for n in ["chat", "embed", "image"]]})
        return httpx.Response(200, json={"data": [
            {"model_name": "chat", "litellm_params": {"api_key": "SECRET"},
             "model_info": {"mode": "chat", "supports_function_calling": True}},
            {"model_name": "embed", "model_info": {"mode": "embedding"}},
            {"model_name": "image", "model_info": {"mode": "image_generation"}},
            {"model_name": "forbidden", "model_info": {"mode": "chat"}},
        ]})

    gateway = Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(handler))
    try:
        models = await gateway.models()
        assert [m.id for m in models] == ["chat"]
        assert "SECRET" not in str(models)
        await gateway.models()
        assert len(calls) == 2
        with pytest.raises(ValueError):
            await gateway.validate_model("forbidden")
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_jev_parallel_requests_preserve_no_log_and_validate_every_answer():
    import json

    active = maximum = 0

    async def handler(request):
        nonlocal active, maximum
        assert request.url.host == "gateway.example"
        assert request.headers["x-litellm-enable-message-redaction"] == "true"
        body = json.loads(request.content)
        assert body["no-log"] is True
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"model": "jev-test", "answers": {
            k: {"type": "noul", "noul": 0.9} for k in body["questions"]
        }})

    gateway = Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1",
                               jev_concurrency=3), httpx.MockTransport(handler))
    try:
        results = await asyncio.gather(*(gateway.classify("work", {"private": "Personal?"}) for _ in range(7)))
        assert maximum == 3
        assert all(r.answers["private"].noul == 0.9 for r in results)
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_jev_errors_do_not_return_private_gateway_response():
    gateway = Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(
        lambda request: httpx.Response(500, text="PRIVATE PROMPT IN UPSTREAM ERROR")))
    try:
        with pytest.raises(GatewayError) as error:
            await gateway.classify("private", {"a": "Question?"})
        assert "PRIVATE" not in str(error.value)
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["1.102.0", "PRIVATE UPSTREAM CONTENT", None])
async def test_missing_jev_explains_gateway_requirement_without_leaking_diagnostics(reported):
    def handler(request):
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"info": {"version": reported}})
        return httpx.Response(404, text="PRIVATE UPSTREAM CONTENT")

    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(handler)) as gateway:
        result = await gateway.probe_jev()
    assert result["status"] == "not_enabled"
    assert "v1.103.0-rc" in result["message"] and "TYPESAFE_API_KEY" in result["message"]
    assert "PRIVATE" not in str(result)
    assert result["gateway_version"] == ("1.102.0" if reported == "1.102.0" else None)


@pytest.mark.asyncio
async def test_version_discovery_failure_preserves_missing_jev_diagnosis():
    def handler(request):
        if request.url.path == "/openapi.json":
            raise httpx.ReadTimeout("PRIVATE UPSTREAM CONTENT", request=request)
        return httpx.Response(404)

    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(handler)) as gateway:
        result = await gateway.probe_jev()
    assert result["status"] == "not_enabled" and result["gateway_version"] is None
    assert "PRIVATE" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('body,status', [
    ({'detail': {'error_type': 'authentication_error', 'message': 'PRIVATE SECRET'}}, 'provider_auth_failed'),
    ({'error': {'type': 'auth_error', 'message': 'PRIVATE SECRET'}}, 'authentication_failed'),
    ({'detail': 'PRIVATE SECRET'}, 'authentication_failed'),
    (['PRIVATE SECRET'], 'authentication_failed'),
])
async def test_jev_auth_diagnosis_distinguishes_provider_key_without_exposing_errors(body, status):
    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(
            lambda request: httpx.Response(401, json=body))) as gateway:
        result = await gateway.probe_jev()
    assert result['status'] == status
    assert 'PRIVATE' not in str(result)
    assert 'HTTP 401' in result['message']
    if status == 'provider_auth_failed':
        assert 'TYPESAFE_API_KEY' in result['message']


@pytest.mark.asyncio
async def test_json_generation_survives_system_messages_becoming_responses_instructions():
    import json

    messages = [{'role': 'system', 'content': 'Summarize as JSON.'},
                {'role': 'user', 'content': 'The demo is planned for Thursday.'}]
    original = [dict(message) for message in messages]

    def responses_gateway(request):
        body = json.loads(request.content)
        inputs = [m for m in body['messages'] if m['role'] != 'system']
        # Responses validates JSON mode against input, excluding instructions.
        assert any('json' in m['content'].lower() for m in inputs)
        assert body['messages'][:2] == original
        assert body['response_format'] == {'type': 'json_object'}
        assert body['no-log'] is True
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})

    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(responses_gateway)) as gateway:
        assert await gateway.generate(messages, json_output=True) == '{"ok":true}'
    assert messages == original


@pytest.mark.asyncio
@pytest.mark.parametrize('http_status,status', [(429, 'rate_limited'), (500, 'unavailable'), (503, 'unavailable')])
async def test_jev_service_failures_offer_retry_without_claiming_integration_is_missing(http_status, status):
    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(
            lambda request: httpx.Response(http_status, json={'detail': 'PRIVATE_UPSTREAM_CONTENT'}))) as gateway:
        result = await gateway.probe_jev()
    assert result['status'] == status
    assert f'HTTP {http_status}' in result['message'] and 'Try again' in result['message']
    assert 'Enable' not in result['message'] and 'PRIVATE_UPSTREAM_CONTENT' not in result['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('content', ['{"ok": true}', '{"title": "Check", "summary": "Reviewed setup", "projects": "invalid"}'])
async def test_background_probe_requires_the_actual_work_record_schema(content):
    def handler(request):
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'candidate'}]})
        if request.url.path == '/model/info':
            return httpx.Response(200, json={'data': []})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})

    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(handler)) as gateway:
        with pytest.raises(GatewayError) as error:
            await gateway.probe_model('candidate', 'generation')
        assert error.value.status == 422
        assert error.value.operation == 'structured background model check'


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapper', ['{}', '```json\n{}\n```', '```\n{}\n```', ' \n```JSON\r\n{}\r\n```\n '])
async def test_background_probe_accepts_plain_or_wholly_fenced_json(wrapper):
    content = '{"title":"Check","summary":"Reviewed workspace onboarding.","projects":[]}'
    def handler(request):
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'candidate'}]})
        if request.url.path == '/model/info':
            return httpx.Response(200, json={'data': []})
        return httpx.Response(200, json={'choices': [{'message': {'content': wrapper.format(content)}}]})
    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(handler)) as gateway:
        assert await gateway.probe_model('candidate', 'generation') == {'status': 'ready'}


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [
    '```json\n{"ok":true}\n```',
    '```json\n{"title":"Check","summary":"PRIVATE_CANARY","projects":"wrong-type"}\n```',
    'Explanation first.\n```json\n{"title":"Check","summary":"PRIVATE_CANARY"}\n```',
    '```json\n{"title":"Check","summary":"PRIVATE_CANARY"}\n```\nUnrequested commentary.',
    '```json\n{"title":"truncated',
])
async def test_background_probe_still_rejects_invalid_or_embedded_json_without_echoing_it(content):
    def handler(request):
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'candidate'}]})
        if request.url.path == '/model/info':
            return httpx.Response(200, json={'data': []})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), httpx.MockTransport(handler)) as gateway:
        with pytest.raises(GatewayError) as error:
            await gateway.probe_model('candidate', 'generation')
        assert 'output did not match the required format' in str(error.value)
        assert 'HTTP' not in str(error.value)
        assert 'PRIVATE_CANARY' not in str(error.value)


@pytest.mark.asyncio
async def test_json_wrapper_normalization_does_not_change_plain_text_generation():
    content = '```json\n{"summary":"Keep the author’s text."}\n```'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': content}}]}))
    async with Gateway(Settings(_env_file=None, litellm_base_url="https://gateway.example/v1"), transport) as gateway:
        assert await gateway.generate([{'role': 'user', 'content': 'Give an example'}]) == content
