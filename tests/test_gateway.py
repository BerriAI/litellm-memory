import asyncio

import httpx
import pytest

from moegent.config import Settings
from moegent.gateway import Gateway, GatewayError


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

    gateway = Gateway(Settings(_env_file=None), httpx.MockTransport(handler))
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
    gateway = Gateway(Settings(_env_file=None), httpx.MockTransport(
        lambda request: httpx.Response(500, text="PRIVATE PROMPT IN UPSTREAM ERROR")))
    try:
        with pytest.raises(GatewayError) as error:
            await gateway.classify("private", {"a": "Question?"})
        assert "PRIVATE" not in str(error.value)
    finally:
        await gateway.close()
