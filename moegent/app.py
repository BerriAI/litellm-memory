import asyncio
import contextlib
import html
import json
import secrets
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from slack_sdk.signature import SignatureVerifier
from starlette.middleware.sessions import SessionMiddleware

from .access import AccessPolicy, Principal
from .agent import answer
from .config import ROOT, Settings
from .memory import memory_call, memory_server, warm_blob_cache
from .slack import Slack
from .storage import Storage
from .worker import worker


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=24000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    history: list[Message] = Field(default_factory=list, max_length=16)


def create_app(settings=None, agent_fn=answer, start_worker=True):
    settings = settings or Settings()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    if not settings.session_secret:
        secret_path = settings.state_dir / "session-secret"
        if not secret_path.exists():
            secret_path.write_text(secrets.token_urlsafe(48))
            secret_path.chmod(0o600)
        settings.session_secret = secret_path.read_text().strip()
    policy = AccessPolicy(settings)
    store = Storage(settings.state_dir / "moegent.sqlite3")
    slack = Slack(settings, store)

    @asynccontextmanager
    async def lifespan(app):
        await store.open()
        tasks = []
        if settings.memory_warm_on_start:
            async def warm():
                with contextlib.suppress(Exception):
                    await warm_blob_cache(settings)
                    async with memory_server(settings) as server:
                        await memory_call(server, "memory_search", {"query": "product priorities", "mode": "hybrid", "limit": 1})
            tasks.append(asyncio.create_task(warm()))
        if start_worker:
            tasks.append(asyncio.create_task(worker(settings, store, slack, agent_fn)))
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await store.close()

    app = FastAPI(title="Moegent", lifespan=lifespan)
    app.state.store, app.state.slack, app.state.settings = store, slack, settings
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="moegent_session",
                       same_site="none" if settings.secure_cookies else "lax", https_only=settings.secure_cookies)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    oauth = OAuth()
    oauth.register("slack", client_id=settings.slack_client_id, client_secret=settings.slack_client_secret,
                   server_metadata_url="https://slack.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid profile"})

    def principal(request):
        data = request.session.get("principal", {})
        return Principal("web", data.get("user_id", ""), data.get("team_id", ""))

    def authorize(request):
        policy.authorize(principal(request))
        if "browser_id" not in request.session:
            request.session["browser_id"] = secrets.token_urlsafe(24)
        return request.session["browser_id"]

    def same_origin(request):
        origin = request.headers.get("origin")
        expected = urlsplit(settings.public_base_url).netloc
        if origin and urlsplit(origin).netloc != expected:
            raise HTTPException(403, "Unexpected origin")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Unexpected origin")

    @app.get("/health")
    async def health():
        return {"status": "ok", "name": "Moegent"}

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static/index.html")

    @app.get("/api/config")
    async def config(request: Request):
        request.session.setdefault("browser_id", secrets.token_urlsafe(24))
        user = principal(request)
        return {"name": "Moegent", "access_mode": settings.access_mode,
                "login_available": bool(settings.slack_client_id), "signed_in": bool(user.user_id),
                "slack_installed": bool(await slack.token())}

    @app.post("/api/chat", status_code=202)
    async def chat(body: ChatRequest, request: Request):
        same_origin(request)
        owner = authorize(request)
        if not body.question.strip():
            raise HTTPException(422, "Enter a question")
        job_id = await store.enqueue(owner, "web", body.model_dump())
        return {"id": job_id, "status": "queued"}

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str, request: Request):
        owner = authorize(request)
        record = await store.get_job(job_id)
        if not record or record["owner"] != owner or record["kind"] != "web":
            raise HTTPException(404, "Request not found")
        return {key: record[key] for key in ["id", "status", "answer", "error"]}

    async def call_memory(tool, arguments):
        try:
            async with memory_server(settings) as server:
                return await memory_call(server, tool, arguments)
        except Exception:
            raise HTTPException(503, "Memory search is temporarily unavailable") from None

    @app.post("/api/memories/search")
    async def search(body: dict, request: Request):
        same_origin(request)
        authorize(request)
        return await call_memory("memory_search", body)

    @app.get("/api/memories/{memory_id}")
    async def read(memory_id: str, request: Request):
        authorize(request)
        return await call_memory("memory_read", {"id": memory_id})

    @app.get("/memory/{memory_id}", response_class=HTMLResponse)
    async def memory_page(memory_id: str, request: Request):
        try:
            authorize(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                return RedirectResponse("/auth/slack")
            raise
        data = await call_memory("memory_read", {"id": memory_id})
        # Escape every source field: memories are untrusted documents.
        content = html.escape(json.dumps(data, indent=2, ensure_ascii=False))
        return HTMLResponse('<!doctype html><meta name="viewport" content="width=device-width">'
                            '<title>Memory · Moegent</title><link rel="stylesheet" href="/static/style.css">'
                            '<main class="memory-page"><a href="/">← Moegent</a><h1>Source memory</h1>'
                            f'<pre>{content}</pre></main>')

    @app.post("/slack/events")
    async def events(request: Request):
        raw = await request.body()
        if not settings.slack_signing_secret or not SignatureVerifier(settings.slack_signing_secret).is_valid_request(
            raw, dict(request.headers)
        ):
            raise HTTPException(401, "Invalid Slack signature")
        try:
            envelope = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "Invalid event") from None
        if envelope.get("type") == "url_verification":
            return {"challenge": envelope["challenge"]}
        if envelope.get("type") == "event_callback" and envelope.get("event_id"):
            await slack.event(envelope)
        return {"ok": True}

    @app.get("/auth/slack")
    async def login(request: Request):
        if not settings.slack_client_id:
            raise HTTPException(503, "Slack sign-in is not configured")
        return await oauth.slack.authorize_redirect(request, settings.public_base_url + "/auth/slack/callback")

    @app.api_route("/auth/slack/callback", methods=["GET", "POST"])
    async def login_callback(request: Request):
        try:
            token = await oauth.slack.authorize_access_token(request)
            info = token["userinfo"]
            user_id = info.get("https://slack.com/user_id") or info["sub"]
            team_id = info["https://slack.com/team_id"]
            user = Principal("web", user_id, team_id)
            policy.authorize(user)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(401, "Slack sign-in failed") from None
        request.session.clear()
        request.session.update({"principal": {"user_id": user_id, "team_id": team_id},
                                "browser_id": secrets.token_urlsafe(24)})
        return RedirectResponse("/", status_code=303)

    @app.post("/auth/logout")
    async def logout(request: Request):
        same_origin(request)
        request.session.clear()
        return {"ok": True}

    @app.get("/slack/install/callback", response_class=HTMLResponse)
    async def install_callback(request: Request, code: str = "", state: str = ""):
        try:
            signed = URLSafeTimedSerializer(settings.session_secret, salt="slack-install").loads(state, max_age=3600)
            if signed != settings.slack_team_id or not code:
                raise ValueError()
        except (BadSignature, ValueError):
            raise HTTPException(400, "Invalid or expired installation link") from None
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://slack.com/api/oauth.v2.access", data={
                "client_id": settings.slack_client_id, "client_secret": settings.slack_client_secret,
                "code": code, "redirect_uri": settings.public_base_url + "/slack/install/callback",
            })
        data = response.json()
        if not data.get("ok"):
            raise HTTPException(400, "Slack installation failed")
        installer = Principal("slack", data.get("authed_user", {}).get("id", ""), data.get("team", {}).get("id", ""))
        if not policy.owner(installer):
            raise HTTPException(403, "Only Moe can install this app")
        await store.setting("slack_bot_token", data["access_token"])
        await store.setting("slack_bot_user_id", data["bot_user_id"])
        return HTMLResponse('<h1>Moegent is connected</h1><p>You can now invite @Moegent to a Slack channel or send it a DM.</p><a href="/">Open Moegent</a>')

    return app


app = create_app()
