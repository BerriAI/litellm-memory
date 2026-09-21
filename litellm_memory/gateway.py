"""All inference and model discovery use the deployment's LiteLLM gateway.

Never return model_info's provider parameters or credentials to a client.
"""

import asyncio
import math
import re
import time
from typing import Literal

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .config import Settings
from .observability import Telemetry


class GatewayError(RuntimeError):
    def __init__(self, operation: str, status: int = 503, *, provider_auth_failed=False):
        self.operation, self.status = operation, status
        self.provider_auth_failed = provider_auth_failed
        super().__init__(f"The gateway could not complete {operation} (HTTP {status}).")


class ModelOption(BaseModel):
    id: str
    mode: str | None = None
    provider: str | None = None
    supports_tools: bool | None = None


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1, allow_inf_nan=False)


class JevResult(BaseModel):
    model: str
    answers: dict[str, NoulAnswer]


class Gateway:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport
        self.root = settings.litellm_base_url.rstrip("/").removesuffix("/v1")
        self.http = httpx.AsyncClient(
            transport=transport, timeout=90,
            headers={"Authorization": f"Bearer {settings.litellm_api_key}",
                     "x-litellm-enable-message-redaction": "true"},
        )
        self._models: list[ModelOption] | None = None
        self._models_until = 0.0
        self._catalog_lock = asyncio.Lock()
        self._jev_slots = asyncio.Semaphore(settings.jev_concurrency)
        self.telemetry = Telemetry()

    async def close(self):
        await self.http.aclose()

    def chat_client(self):
        return AsyncOpenAI(
            base_url=self.settings.litellm_base_url, api_key=self.settings.litellm_api_key,
            timeout=90, max_retries=0,
            default_headers={"x-litellm-enable-message-redaction": "true"},
        )

    def snapshot(self, **overrides):
        """An independent client freezes credentials/model roles for an in-flight job."""
        frozen = Gateway(self.settings.model_copy(update=overrides), self.transport)
        frozen._jev_slots = self._jev_slots
        frozen.telemetry = self.telemetry
        if not {"litellm_base_url", "litellm_api_key"}.intersection(overrides):
            frozen._models, frozen._models_until = self._models, self._models_until
        return frozen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def models(self, refresh=False, kind="chat") -> list[ModelOption]:
        catalog = await self.catalog(refresh)
        if kind == "all":
            return catalog
        if kind == "embedding":
            return [m for m in catalog if m.mode in (None, "embedding")]
        if kind == "generation":
            return [m for m in catalog if m.mode in (None, "chat", "responses")]
        return [m for m in catalog if m.mode in (None, "chat", "responses") and m.supports_tools is not False]

    async def catalog(self, refresh=False) -> list[ModelOption]:
        async with self._catalog_lock:
            if not refresh and self._models is not None and time.monotonic() < self._models_until:
                return self._models
            listing, info = await asyncio.gather(
                self.http.get(self.root + "/v1/models"),
                self.http.get(self.root + "/model/info"),
            )
            if not listing.is_success:
                raise GatewayError("model discovery", listing.status_code)
            # The key-scoped model list is authoritative. Info enriches only those IDs.
            metadata = {}
            if info.is_success:
                for row in info.json().get("data", []):
                    details = row.get("model_info") or {}
                    metadata[row.get("model_name")] = {
                        "mode": details.get("mode"),
                        "provider": details.get("litellm_provider"),
                        "supports_tools": details.get("supports_function_calling"),
                    }
            options = {}
            for row in listing.json().get("data", []):
                name = row.get("id")
                if not isinstance(name, str):
                    continue
                details = metadata.get(name, {})
                options[name] = ModelOption(id=name, **details)
            self._models = sorted(options.values(), key=lambda m: m.id.casefold())
            self._models_until = time.monotonic() + self.settings.model_catalog_ttl
            return self._models

    async def validate_model(self, model: str, kind="chat") -> str:
        if not any(option.id == model for option in await self.models(kind=kind)):
            raise ValueError(f"Choose an available {kind} model from the LiteLLM gateway.")
        return model

    async def probe_model(self, model: str, kind="chat"):
        await self.validate_model(model, kind)
        if kind == "embedding":
            vector = (await self.embeddings(["LiteLLM Memory connection check"], model=model))[0]
            return {"status": "ready", "dimensions": len(vector)}
        if kind == "generation":
            import json
            try:
                result = await self.generate([{"role": "user", "content": 'Return JSON: {"ok": true}'}],
                                             model=model, json_output=True)
                if not isinstance(json.loads(result), dict):
                    raise ValueError()
            except (ValueError, TypeError):
                raise GatewayError("structured background model check", 422) from None
            return {"status": "ready"}
        response = await self.http.post(self.root + "/v1/chat/completions", json={
            "model": model, "messages": [{"role": "user", "content": "Call connection_check once."}],
            "tools": [{"type": "function", "function": {"name": "connection_check",
                "description": "Check the connection", "parameters": {"type": "object", "properties": {},
                    "additionalProperties": False}}}],
            "tool_choice": {"type": "function", "function": {"name": "connection_check"}},
            "max_tokens": 64, "no-log": True,
        })
        if not response.is_success:
            raise GatewayError("agent model check", response.status_code)
        try:
            calls = response.json()["choices"][0]["message"]["tool_calls"]
            if not any(call["function"]["name"] == "connection_check" for call in calls):
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise GatewayError("tool-capable agent model check", 422) from None
        return {"status": "ready"}

    async def probe_jev(self, model=None):
        try:
            result = await self.classify("LiteLLM Memory setup check. This is a synthetic test.",
                                         {"test": "Is this a synthetic test?"}, model=model)
            return {"status": "ready", "model": result.model, "required": True}
        except GatewayError as exc:
            if exc.provider_auth_failed:
                return {"status": "provider_auth_failed", "required": True, "message": (
                    "TypeSafe rejected the credential supplied by your LiteLLM gateway (HTTP 401)."
                    " Check TYPESAFE_API_KEY or the gateway's TypeSafe pass-through credentials, then try again.")}
            status = {401: "authentication_failed", 403: "access_denied", 404: "not_enabled", 429: "rate_limited"}.get(
                exc.status, "unavailable")
            if exc.status in {401, 403}:
                return {"status": status, "required": True, "message": (
                    f"The TypeSafe Jev request was denied (HTTP {exc.status}). Check this LiteLLM key's route access"
                    " and the gateway's TypeSafe credential.")}
            if exc.status == 404:
                version = await self.reported_version()
                reported = f" This gateway reports LiteLLM {version}." if version else ""
                return {"status": status, "required": True, "gateway_version": version,
                        "message": (
                            "The gateway returned HTTP 404 for TypeSafe Jev." + reported +
                            " Built-in Jev support starts in LiteLLM v1.103.0-rc."
                            " Use a release with Jev support, configure TYPESAFE_API_KEY on the gateway,"
                            " and check that its TypeSafe route is accessible to your LiteLLM key.")}
            return {"status": status, "required": True, "message": (
                "LiteLLM Memory needs TypeSafe Jev. Enable it on your LiteLLM gateway and allow this key to use it.")}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"status": "unavailable", "required": True,
                    "message": "The gateway did not return a valid Jev response. Check its TypeSafe integration."}

    async def reported_version(self) -> str | None:
        """Best-effort setup diagnostic, never a substitute for a real capability probe."""
        try:
            response = await self.http.get(self.root + "/openapi.json", timeout=5)
            if not response.is_success:
                return None
            version = response.json()["info"]["version"]
            # Return only a bounded version identifier, never arbitrary gateway content.
            if isinstance(version, str) and re.fullmatch(r"v?\d+\.\d+\.\d+[A-Za-z0-9.+-]{0,48}", version):
                return version
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
        return None

    async def classify(self, state: str, questions: dict[str, str], *, model=None) -> JevResult:
        with self.telemetry.operation('classification', model=model or self.settings.jev_model,
                                      criterion_count=len(questions)):
            if not questions:
                return JevResult(model=self.settings.jev_model, answers={})
            # Paths are relative to the same gateway; configuration cannot select a provider host.
            path = self.settings.jev_path
            if not path.startswith("/") or path.startswith("//") or "://" in path:
                raise ValueError("Jev must use a path on the configured LiteLLM gateway.")
            body = {"model": model or self.settings.jev_model, "state": state, "no-log": True,
                    "questions": {key: {"type": "noul", "instructions": question}
                                  for key, question in questions.items()}}
            async with self._jev_slots:
                response = await self.http.post(self.root + path, json=body)
            if not response.is_success:
                # TypeSafe's known error envelope distinguishes its credential from
                # the client's LiteLLM key. Never retain or expose upstream text.
                provider_auth_failed = False
                if response.status_code == 401:
                    try:
                        detail = response.json().get("detail")
                        provider_auth_failed = (isinstance(detail, dict)
                                                and detail.get("error_type") == "authentication_error")
                    except (ValueError, AttributeError):
                        pass
                raise GatewayError("Jev classification", response.status_code,
                                   provider_auth_failed=provider_auth_failed)
            result = JevResult.model_validate(response.json())
            if set(result.answers) != set(questions):
                raise GatewayError("complete Jev classification")
            return result

    async def embeddings(self, texts: list[str], *, model=None) -> list[list[float]]:
        with self.telemetry.operation('embedding', model=model or self.settings.memory_embedding_model,
                                      item_count=len(texts)):
            response = await self.http.post(self.root + "/v1/embeddings", json={
                "model": model or self.settings.memory_embedding_model, "input": texts, "no-log": True,
            })
            if not response.is_success:
                raise GatewayError("embeddings", response.status_code)
            try:
                data = sorted(response.json()["data"], key=lambda row: row["index"])
                vectors = [row["embedding"] for row in data]
                if ([row["index"] for row in data] != list(range(len(texts))) or not vectors
                        or not vectors[0] or any(len(v) != len(vectors[0]) for v in vectors)
                        or any(isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n)
                               for v in vectors for n in v)):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise GatewayError("complete finite embeddings", 502) from None
            return vectors

    async def generate(self, messages: list[dict], *, model: str | None = None, json_output=False):
        with self.telemetry.operation(
                'generation', model=model or self.settings.background_model or self.settings.litellm_model):
            body = {"model": model or self.settings.background_model or self.settings.litellm_model,
                    "messages": messages, "no-log": True}
            if json_output:
                body["response_format"] = {"type": "json_object"}
            response = await self.http.post(self.root + "/v1/chat/completions", json=body)
            if not response.is_success:
                raise GatewayError("generation", response.status_code)
            return response.json()["choices"][0]["message"]["content"]
