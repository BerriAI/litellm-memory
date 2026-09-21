import asyncio
import hashlib
import io
import json
import os
import tarfile
from contextlib import asynccontextmanager

import httpx
from agents.mcp import MCPServerStdio, create_static_tool_filter

from .config import Settings

# Bound subprocess memory use on the 512 MB Render instance.
MEMORY_SLOTS = asyncio.Semaphore(2)

READ_TOOLS = ["memory_catalog", "memory_search", "memory_read"]


class MemoryNotFound(Exception):
    pass


@asynccontextmanager
async def memory_server(settings: Settings):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "MEMORY_REPO": settings.memory_repo,
        "MEMORY_ALLOWED_ACTORS": settings.memory_allowed_actors,
        "MEMORY_STATE_DIR": str(settings.memory_state_dir),
        "MEMORY_EMBEDDING_BASE_URL": settings.litellm_base_url,
        "MEMORY_EMBEDDING_API_KEY": settings.litellm_api_key,
        "MEMORY_EMBEDDING_MODEL": settings.memory_embedding_model,
        "MEMORY_EMBEDDING_VERSION": settings.memory_embedding_version,
    }
    if settings.gh_token:
        env["GH_TOKEN"] = settings.gh_token
    async with MEMORY_SLOTS, MCPServerStdio(
        name="product-team-knowledge",
        params={"command": "node", "args": [str(settings.memory_script), "serve"], "env": env},
        client_session_timeout_seconds=240,
        cache_tools_list=True,
        tool_filter=create_static_tool_filter(allowed_tool_names=READ_TOOLS),
    ) as server:
        yield server


async def memory_call(server, name: str, args: dict):
    if name not in READ_TOOLS:
        raise ValueError("Only read-only memory tools are exposed")
    result = await server.call_tool(name, args)
    if result.is_error:
        if any("Memory not found" in getattr(item, "text", "") for item in result.content):
            raise MemoryNotFound()
        raise RuntimeError("Memory request failed")
    return json.loads(result.content[0].text)


async def warm_blob_cache(settings: Settings):
    """One archive download avoids hundreds of API calls on the first cold read."""
    cache = settings.memory_state_dir / "blobs"
    if cache.exists() and any(cache.iterdir()):
        return
    if not settings.gh_token:
        return  # Local development can use the signed-in gh CLI directly.
    async with httpx.AsyncClient(follow_redirects=True, timeout=90) as client:
        response = await client.get(
            f"https://api.github.com/repos/{settings.memory_repo}/tarball/main",
            headers={"Authorization": f"Bearer {settings.gh_token}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
    def unpack():
        cache.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
            for member in archive:
                parts = member.name.split("/")
                if not member.isfile() or len(parts) < 3 or parts[1] not in {"knowledge", "journal", "archive"}:
                    continue
                if member.size > 200_000:
                    continue
                data = archive.extractfile(member).read()
                digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
                (cache / digest).write_bytes(data)
    await asyncio.to_thread(unpack)
