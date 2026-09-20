"""Narrow adapter to Ente Museum's authenticated encrypted entity API.

No direct writes to Museum's database. A dedicated service account stores opaque
encrypted entities; the application owns per-member keys and authorization.
"""

import asyncio

import httpx


class EnteError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Encrypted storage is unavailable (HTTP {status}).")


class Museum:
    def __init__(self, base_url: str, token: str, transport=None, namespace_key=None):
        self.namespace_key = namespace_key
        self.namespace_ready = False
        self.namespace_lock = asyncio.Lock()
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=45, transport=transport,
            headers={"X-Auth-Token": token, "X-Client-Package": "io.ente.photos",
                     "User-Agent": "LiteLLM-Memory/1.0"},
        )

    async def close(self):
        await self.http.aclose()

    async def ensure_namespace(self):
        if not self.namespace_key or self.namespace_ready:
            return
        async with self.namespace_lock:
            if self.namespace_ready:
                return
            response = await self.http.get("/user-entity/key", params={"type": "memory"})
            if response.status_code == 404:
                created = await self.http.post("/user-entity/key", json={"type": "memory", **self.namespace_key})
                if not created.is_success and created.status_code not in {409, 500}:
                    raise EnteError(created.status_code)
                response = await self.http.get("/user-entity/key", params={"type": "memory"})
            if not response.is_success:
                raise EnteError(response.status_code)
            self.namespace_ready = True

    async def put(self, entity_id: str, blob: dict, *, existing=False):
        await self.ensure_namespace()
        body = {"id": entity_id, "type": "memory", **blob}
        method = self.http.put if existing else self.http.post
        response = await method("/user-entity/entity", json=body)
        # A create may have committed before a lost response. Updating our stable ID
        # makes retries idempotent. Never switch IDs and duplicate a source event.
        if not existing and response.status_code in {409, 500}:
            response = await self.http.put("/user-entity/entity", json=body)
        if not response.is_success:
            raise EnteError(response.status_code)
        data = response.json()
        if data.get("id") != entity_id:
            raise EnteError(502)
        return data

    async def delete(self, entity_id: str):
        response = await self.http.delete("/user-entity/entity", params={"id": entity_id})
        if not response.is_success:
            raise EnteError(response.status_code)

    async def diff(self, since=0, limit=500):
        response = await self.http.get("/user-entity/entity/diff", params={
            "type": "memory", "sinceTime": since, "limit": limit,
        })
        if not response.is_success:
            raise EnteError(response.status_code)
        return response.json()["diff"]
