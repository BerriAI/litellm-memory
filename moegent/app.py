import asyncio
import contextlib
import json
import secrets
from contextlib import asynccontextmanager
from html import escape
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

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
from .memory import MemoryNotFound, memory_call, memory_server, warm_blob_cache
from .slack import Slack
from .storage import Storage
from .worker import worker


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=24000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    history: list[Message] = Field(default_factory=list, max_length=16)
    request_id: UUID | None = None


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
    running = {}
    slack.running = running

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
            tasks.append(asyncio.create_task(worker(settings, store, slack, agent_fn, running)))
            tasks.append(asyncio.create_task(slack.monitor()))
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await slack.close()
        await store.close()

    app = FastAPI(title="litemem", lifespan=lifespan)
    app.state.store, app.state.slack, app.state.settings = store, slack, settings
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="moegent_session",
                       same_site="none" if settings.secure_cookies else "lax", https_only=settings.secure_cookies)
    oauth = OAuth()
    oauth.register("slack", client_id=settings.slack_client_id, client_secret=settings.slack_client_secret,
                   server_metadata_url="https://slack.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid profile"})
    app.state.oauth = oauth

    def return_path(value):
        # Only app pages can be OAuth return destinations, never external URLs or auth routes.
        if any(ord(char) < 32 for char in value) or "\\" in value or value.startswith("//"):
            return "/"
        try:
            parsed = urlsplit(value)
        except ValueError:
            return "/"
        if parsed.scheme or parsed.netloc:
            return "/"
        if parsed.path in {"/", "/sources"} or parsed.path.startswith("/memory/"):
            return value
        return "/"

    def auth_page(title, message, status_code=200, next_path="/"):
        template = (ROOT / "static/auth.html").read_text()
        values = {"TITLE": title, "MESSAGE": message,
                  "LOGIN_URL": "/auth/slack?" + urlencode({"next": return_path(next_path)})}
        for key, value in values.items():
            template = template.replace("{{" + key + "}}", escape(value, quote=True))
        return HTMLResponse(template, status_code=status_code, headers={"Cache-Control": "no-store"})

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
        return {"status": "ok", "name": "litemem"}

    @app.get("/")
    @app.get("/static/index.html", include_in_schema=False)
    @app.get("/sources")
    @app.get("/memory/{memory_id}")
    async def index(request: Request):
        try:
            authorize(request)
        except HTTPException as exc:
            target = return_path(request.url.path + ("?" + request.url.query if request.url.query else ""))
            if exc.status_code == 401:
                return RedirectResponse("/auth/slack?" + urlencode({"next": target}), status_code=303,
                                        headers={"Cache-Control": "no-store"})
            return auth_page("This account doesn’t have access", "Sign in with an approved Slack account.",
                             status_code=403, next_path=target)
        return FileResponse(ROOT / "static/index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/config")
    async def config(request: Request):
        request.session.setdefault("browser_id", secrets.token_urlsafe(24))
        user = principal(request)
        return {"name": "litemem", "access_mode": settings.access_mode,
                "login_available": bool(settings.slack_client_id), "signed_in": bool(user.user_id),
                "slack_installed": bool(await slack.token())}

    @app.post("/api/chat", status_code=202)
    async def chat(body: ChatRequest, request: Request):
        same_origin(request)
        owner = authorize(request)
        if not body.question.strip():
            raise HTTPException(422, "Enter a question")
        job_id = await store.enqueue(owner, "web", body.model_dump(mode="json"),
                                     f"web:{owner}:{body.request_id}" if body.request_id else None)
        return {"id": job_id, "status": "queued"}

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str, request: Request):
        owner = authorize(request)
        record = await store.get_job(job_id)
        if not record or record["owner"] != owner or record["kind"] != "web":
            raise HTTPException(404, "Request not found")
        return {key: record[key] for key in ["id", "status", "answer", "error", "phase", "partial", "created"]}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        same_origin(request)
        owner = authorize(request)
        record = await store.get_job(job_id)
        if not record or record["owner"] != owner or record["kind"] != "web":
            raise HTTPException(404, "Request not found")
        await store.cancel(job_id)
        if task := running.get(job_id):
            task.cancel()
        return {"status": (await store.get_job(job_id))["status"]}

    async def call_memory(tool, arguments):
        try:
            async with memory_server(settings) as server:
                return await memory_call(server, tool, arguments)
        except MemoryNotFound:
            raise HTTPException(404, "This memory is unavailable or no longer shared.") from None
        except Exception:
            raise HTTPException(503, "Memory search is temporarily unavailable") from None

    @app.post("/api/memories/search")
    async def search(body: dict, request: Request):
        same_origin(request)
        authorize(request)
        for field in ["created", "updated"]:
            if body.get(field + "_from") and body.get(field + "_before"):
                if body[field + "_from"] >= body[field + "_before"]:
                    raise HTTPException(422, "The end date must be later than the start date.")
        return await call_memory("memory_search", body)

    @app.get("/api/memories/{memory_id}")
    async def read(memory_id: str, request: Request):
        authorize(request)
        return await call_memory("memory_read", {"id": memory_id})

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
            return auth_page("Sign-in is unavailable", "Slack sign-in hasn’t been configured yet.", 503)
        request.session["login_next"] = return_path(request.query_params.get("next", "/"))
        try:
            return await oauth.slack.authorize_redirect(
                request, settings.public_base_url + "/auth/slack/callback",
                team=settings.slack_team_id, response_mode="form_post",
            )
        except Exception:
            return auth_page("Couldn’t connect to Slack", "Try signing in again in a moment.", 503,
                             request.session.get("login_next", "/"))

    @app.api_route("/auth/slack/callback", methods=["GET", "POST"])
    async def login_callback(request: Request):
        target = return_path(request.session.get("login_next", "/"))
        try:
            token = await oauth.slack.authorize_access_token(request)
            info = token["userinfo"]
            user_id = info.get("https://slack.com/user_id") or info["sub"]
            team_id = info["https://slack.com/team_id"]
            user = Principal("web", user_id, team_id)
            policy.authorize(user)
        except HTTPException:
            request.session.clear()
            return auth_page("This account doesn’t have access",
                             "litemem is available to Moe, Tin, Krrish, and Ishaan. Switch to your approved account in Slack, then try again.",
                             403, target)
        except Exception:
            request.session.clear()
            return auth_page("Sign-in wasn’t completed", "The sign-in was cancelled or expired. Try again.",
                             401, target)
        request.session.clear()
        request.session.update({"principal": {"user_id": user_id, "team_id": team_id},
                                "browser_id": secrets.token_urlsafe(24)})
        return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})

    @app.get("/auth/signed-out")
    async def signed_out():
        return auth_page("You’re signed out", "Sign in with Slack to use litemem.")

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
        await store.setting("slack_scopes", data.get("scope", ""))
        return RedirectResponse("/?slack=connected", status_code=303)

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()
