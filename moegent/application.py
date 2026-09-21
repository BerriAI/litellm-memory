"""litemem application API and web/Slack/MCP adapters."""

import asyncio
import contextlib
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from slack_sdk.signature import SignatureVerifier
from starlette.middleware.sessions import SessionMiddleware

from .archive import Archive, Reader, WorkInput
from .config import ROOT, Settings
from .crypto import KeyVault
from .ente import EnteError, Museum
from .gateway import Gateway, GatewayError
from .guards import RequestGuard
from .mcp_api import build_mcp
from .memory_slack import MemorySlack
from .migration import Invitation, Packet, ReceiveRequest, Transfers, origin
from .model_settings import ModelConfiguration, ModelSettingsService
from .oauth import install_oauth
from .observability import configure as configure_telemetry
from .processing import cleanup_worker, processing_worker
from .query import answer_work
from .query_worker import query_worker
from .setup import DeploymentConfig, install_setup
from .storage import QueueFull, Storage
from .time_settings import DisplayPreferences, ScheduleUpdate, Timezone, date_boundary
from .transfer import ExportStream
from .view_preferences import ViewPreferences
from .work_views import ViewRequest, WorkViews


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    model: str | None = Field(default=None, max_length=200)
    scope: str | None = Field(default=None, max_length=200)
    audience: Literal["owner", "public"] = "owner"
    previous_job_id: UUID | None = None
    request_id: UUID | None = None


class GatewayKey(BaseModel):
    api_key: str = Field(min_length=1, max_length=8000, repr=False)


class AdminTransfer(BaseModel):
    owner: str = Field(min_length=3, max_length=200)


class Search(BaseModel):
    query: str = Field(default="", max_length=2000)
    owner: str | None = None
    visibility: Literal["private", "public"] | None = None
    kind: str | None = None
    source: str | None = None
    project: str | None = Field(default=None, max_length=200)
    created_from: str | None = Field(default=None, max_length=50)
    created_before: str | None = Field(default=None, max_length=50)
    date_timezone: Timezone = 'UTC'
    mode: Literal["keywords", "hybrid"] = "hybrid"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=100)


class SaveModels(BaseModel):
    models: ModelConfiguration
    revision: int = Field(ge=0)


class ExportOptions(BaseModel):
    encrypted: bool = False
    passphrase: str = Field(default='', max_length=1024, repr=False)


class Correction(BaseModel):
    content: str = Field(min_length=1, max_length=200000)
    title: str = Field(default='', max_length=300)
    reason: str = Field(default='', max_length=2000)
    request_id: UUID | None = None
    occurred_at: datetime | None = None


def create_app(settings=None, *, museum=None, gateway=None, start_workers=True, agent_fn=answer_work):
    settings = settings or Settings()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    vault = KeyVault(settings.state_dir, settings.vault_wrapping_key)
    deployment = DeploymentConfig(settings, vault)
    if not settings.session_secret:
        path = settings.state_dir / "session-secret"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48))
            path.chmod(0o600)
        settings.session_secret = path.read_text().strip()
    gateway = gateway or Gateway(settings)
    gateway.settings = settings
    telemetry = configure_telemetry(settings)
    gateway.telemetry = telemetry
    gateway.root = settings.litellm_base_url.rstrip("/").removesuffix("/v1")
    gateway.http.headers["Authorization"] = "Bearer " + settings.litellm_api_key
    if museum is None and settings.ente_base_url and settings.ente_auth_token:
        museum = Museum(settings.ente_base_url, settings.ente_auth_token, namespace_key=vault.namespace_key())
    archive = Archive(settings.state_dir / "memory.sqlite3", vault, museum, sharing_enabled=settings.sharing_enabled)
    transfers = Transfers(settings, archive)
    archive.search_index.budget = settings.search_cache_mb * 1024 * 1024
    store = Storage(settings.state_dir / "moegent.sqlite3", vault)
    model_service = ModelSettingsService(settings, store, gateway, archive)
    views = WorkViews(archive, gateway, deployment)
    archive.views = views
    # Keep the existing installed Slack app and approved channels; the new data layer
    # further limits every access to an enrolled person and the actual delivery audience.
    slack_settings = settings.model_copy()
    slack_settings.access_mode = "allowlist"
    slack_settings.allowed_slack_users = f"{settings.slack_team_id}:{settings.slack_owner_user_id}"
    slack = MemorySlack(slack_settings, store, archive)
    slack.model_settings = settings
    archive.check_eligibility = slack.verify_member
    running = {}
    export_slots = asyncio.Semaphore(2)
    slack.running = running
    mcp, mcp_http = build_mcp(settings, archive, gateway)

    @asynccontextmanager
    async def lifespan(app):
        await store.open()
        await archive.open()
        await transfers.open()
        await views.open()
        await model_service.load()
        slack.settings.allowed_slack_users = ",".join(p["id"] for p in await archive.people(
            Reader(f"{settings.slack_team_id}:{settings.slack_owner_user_id}")))
        tasks = []
        async with mcp_http.router.lifespan_context(mcp_http):
            if start_workers and not settings.recovery_mode:
                tasks += [asyncio.create_task(query_worker(settings, store, slack, archive, gateway, running, agent_fn))
                          for _ in range(2)]
                tasks.append(asyncio.create_task(slack.monitor()))
                tasks.append(asyncio.create_task(slack.reconcile_members()))
                if museum:
                    tasks.append(asyncio.create_task(transfers.worker()))
                    tasks.append(asyncio.create_task(cleanup_worker(archive)))
                    tasks.append(asyncio.create_task(views.worker()))
                    # Separate worker pools keep historical imports from occupying live capacity.
                    tasks += [asyncio.create_task(processing_worker(archive, gateway, historical=historical))
                              for historical in [False, False, True, True]]
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                await slack.close()
                await gateway.close()
                if museum and hasattr(museum, "close"):
                    await museum.close()
                await archive.close()
                await transfers.close()
                await store.close()

    app = FastAPI(title="litemem API", version="1.0.0", lifespan=lifespan)
    api = APIRouter()
    app.state.archive, app.state.store, app.state.gateway = archive, store, gateway
    app.state.transfers = transfers
    app.state.slack, app.state.settings = slack, settings
    app.state.model_service = model_service
    app.state.deployment = deployment
    store.max_pending_per_owner = settings.max_pending_queries_per_member
    store.max_pending = settings.max_pending_queries
    app.state.oauth_lock = asyncio.Lock()
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="litellm_memory_session", same_site="none" if settings.secure_cookies else "lax",
                       https_only=settings.secure_cookies)
    oauth = OAuth()
    oauth.register("slack", client_id=settings.slack_client_id, client_secret=settings.slack_client_secret,
                   server_metadata_url="https://slack.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid profile email"})
    app.state.oauth = oauth

    @app.middleware("http")
    async def security_headers(request, call_next):
        if settings.recovery_mode and request.url.path != '/health':
            return JSONResponse({'detail': 'This restored installation is paused for verification.'}, status_code=503,
                                headers={'Cache-Control': 'no-store', 'Retry-After': '300'})
        try:
            size = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse({"detail": "Invalid request size."}, status_code=400)
        if size < 0 or size > 1_000_000:
            return JSONResponse({"detail": "Split this request into smaller captures."}, status_code=413)
        with telemetry.operation('request') as operation:
            response = await call_next(request)
            operation.set(status_code=response.status_code)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://slack.com")
        if request.url.path == '/transfer/send':
            # Only the transfer page can connect to its one approved receiving
            # origin. The main application's CSP remains self-only.
            try:
                destination = origin(request.query_params.get('destination', ''))
                response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace(
                    "connect-src 'self';", f"connect-src 'self' {destination};")
            except ValueError:
                pass
        # Revalidate modules after deployment; a cached setup module must not
        # keep rendering old readiness conditions alongside a newer backend.
        response.headers["Cache-Control"] = "no-cache" if request.url.path.startswith('/static/') else "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        # Pydantic/provider failures can embed the entire private input in str(exc).
        message = str(exc) if type(exc) is ValueError else "This request could not be validated."
        return JSONResponse({"detail": message}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, exc):
        return JSONResponse({"detail": "Check the required fields and their allowed lengths/types."}, status_code=422)

    @app.exception_handler(QueueFull)
    async def queue_full(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=429, headers={'Retry-After': '30'})

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(LookupError)
    async def unavailable(request, exc):
        return JSONResponse({"detail": "This memory is unavailable."}, status_code=404)

    @app.exception_handler(EnteError)
    @app.exception_handler(GatewayError)
    async def upstream(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    def same_origin(request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != settings.public_base_url.rstrip("/"):
            raise HTTPException(403, "Unexpected origin")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Unexpected origin")

    def session_identity(request):
        identity = request.session.get("identity")
        if not identity:
            raise HTTPException(401, "Sign in with Slack to continue.")
        if identity.get("team_id") != settings.slack_team_id:
            raise HTTPException(403, "This Slack workspace is not eligible.")
        if settings.registration_mode == "owner_only" and identity["user_id"] != settings.slack_owner_user_id:
            raise HTTPException(403, "This rollout is currently limited to its owner.")
        email = identity.get("email", "").casefold()
        if not identity.get("email_verified") or email.rsplit("@", 1)[-1] != settings.allowed_email_domain.casefold():
            raise HTTPException(403, "Sign in with your verified company Slack account.")
        return identity

    async def account(request: Request):
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            owner = await archive.token_owner(auth[7:])
            if not owner:
                raise HTTPException(401, "This connection has expired or been revoked.")
            return owner
        identity = session_identity(request)
        owner = f"{identity['team_id']}:{identity['user_id']}"
        if not await archive.member(owner):
            raise HTTPException(401, "Finish setting up your memory account.")
        if not await slack.verify_member(owner):
            raise HTTPException(403, 'This Slack account is no longer eligible.')
        return owner

    async def browser_account(request):
        # API/MCP tokens cannot widen sharing, mint new credentials, or manage membership.
        if request.headers.get("authorization"):
            raise HTTPException(403, "Open the dashboard to manage your account.")
        same_origin(request)
        return await account(request)

    async def admin_account(request):
        owner = await browser_account(request)
        if owner != f"{settings.slack_team_id}:{settings.slack_owner_user_id}":
            raise HTTPException(403, "Only a workspace administrator can change these settings.")
        return owner

    app.state.account = account
    install_oauth(app, settings, store, archive, browser_account, same_origin)
    install_setup(app, api, deployment, settings, store, archive, gateway, model_service, slack, oauth, same_origin)

    def safe_return(path):
        if any(ord(c) < 32 for c in path) or "\\" in path or path.startswith("//"):
            return "/"
        parts = urlsplit(path)
        if parts.scheme or parts.netloc:
            return "/"
        return path if parts.path in {"/", "/oauth/authorize", "/transfer/receive", "/transfer/send"} or parts.path.startswith("/memory/") else "/"

    @app.get("/health")
    async def health():
        return {"status": "recovery" if settings.recovery_mode else "ok", "name": "litemem",
                "storage": "ente" if museum else "unconfigured"}

    @app.get("/")
    @app.get("/memory/{memory_id}")
    @app.get("/sources")
    async def index():
        return FileResponse(ROOT / "static/memory.html")

    @api.get("/me")
    async def me(request: Request):
        try:
            identity = session_identity(request)
        except HTTPException:
            return {"signed_in": False, "login_available": bool(settings.slack_client_id),
                    "name": "litemem"}
        owner = f"{identity['team_id']}:{identity['user_id']}"
        member = await archive.member(owner)
        if member and not await slack.verify_member(owner):
            member = None
        departed = await (await archive.db.execute('SELECT id FROM members WHERE id=? AND active=0', (owner,))).fetchone()
        return {"signed_in": True, "enrolled": bool(member), 'can_rejoin': bool(departed), "id": owner, "profile": {
                    k: identity.get(k, "") for k in ["name", "avatar", "email"]},
                "sharing_enabled": settings.sharing_enabled, "registration_mode": settings.registration_mode,
                "capture_enabled": bool(member and member["capture_enabled"]),
                "agent_enabled": bool(member and member["agent_enabled"]),
                "is_admin": identity["user_id"] == settings.slack_owner_user_id,
                "stats": await archive.stats(owner) if member else None,
                "display_timezone": (await archive.display_preferences(owner)).timezone if member else None,
                "default_model": settings.litellm_model, "storage_ready": bool(museum),
                "mcp_url": settings.public_base_url.rstrip("/") + "/mcp"}

    @api.post("/signup")
    async def signup(request: Request):
        same_origin(request)
        if not museum:
            raise HTTPException(503, "Encrypted storage is being configured. Please try again shortly.")
        identity = session_identity(request)
        owner = f"{identity['team_id']}:{identity['user_id']}"
        if not await slack.verify_member(owner, fresh=True):
            raise HTTPException(403, 'This Slack account is no longer eligible.')
        await archive.enroll(owner, {k: identity.get(k, "") for k in ["name", "avatar", "email"]})
        people = await archive.people(Reader(owner))
        slack.settings.allowed_slack_users = ",".join(p["id"] for p in people)
        return {"id": owner, "mcp_url": settings.public_base_url.rstrip("/") + "/mcp"}

    @api.post('/rejoin')
    async def rejoin(request: Request):
        same_origin(request)
        if request.headers.get('authorization'):
            raise HTTPException(403, 'Sign in with Slack to restore your account.')
        identity = session_identity(request)
        owner = f"{identity['team_id']}:{identity['user_id']}"
        if not await slack.verify_member(owner, fresh=True):
            raise HTTPException(403, 'This Slack account is no longer eligible.')
        await archive.rejoin(owner, {k: identity.get(k, '') for k in ['name', 'avatar', 'email']})
        slack.settings.allowed_slack_users = ','.join(p['id'] for p in await archive.people(Reader(owner)))
        return {'status': 'restored', 'capture_enabled': False, 'sharing_restored': False}

    @api.get("/models")
    async def models(request: Request):
        await account(request)
        defaults = await model_service.current()
        return {"data": [m.model_dump() for m in await gateway.models()], "default": defaults.agent_model}

    @api.get("/admin/models")
    async def admin_models(request: Request):
        await admin_account(request)
        if request.query_params.get("refresh") == "true":
            await gateway.catalog(refresh=True)
        data = await model_service.describe()
        data["catalogs"] = {kind: [m.model_dump() for m in await gateway.models(kind=kind)]
                            for kind in ["chat", "embedding", "generation"]}
        return data

    @api.get('/admin/connection')
    async def gateway_connection(request: Request):
        await admin_account(request)
        return {'base_url': settings.litellm_base_url, 'key_configured': bool(settings.litellm_api_key)}

    @api.put('/admin/connection/key')
    async def rotate_gateway_key(body: GatewayKey, request: Request):
        await admin_account(request)
        # Key rotation preserves the gateway and configured model IDs. Every
        # role is checked with the candidate before replacing a working key.
        async with deployment.lock, model_service.lock:
            config = await model_service.current()
            candidate_settings = settings.model_copy(update={'litellm_api_key': body.api_key})
            async with Gateway(candidate_settings, gateway.transport) as candidate:
                await candidate.catalog(refresh=True)
                for model, kind in {(config.agent_model, 'chat'),
                                    (config.slack_model or config.agent_model, 'chat'),
                                    (config.background_model or config.agent_model, 'generation'),
                                    (config.embedding_model, 'embedding')}:
                    await candidate.validate_model(model, kind)
                    await candidate.probe_model(model, kind)
                jev = await candidate.probe_jev(config.classifier_model)
                if jev['status'] != 'ready':
                    raise ValueError(jev['message'])
            deployment.save(config={'litellm_api_key': body.api_key}, jev=jev)
            gateway.http.headers['Authorization'] = 'Bearer ' + body.api_key
            gateway._models, gateway._models_until = None, 0
        return {'status': 'updated'}

    @api.post('/admin/transfer')
    async def transfer_admin(body: AdminTransfer, request: Request):
        await admin_account(request)
        if settings.registration_mode == 'owner_only':
            raise HTTPException(409, 'Administrator transfer is unavailable during an owner-only pilot.')
        target = await archive.member(body.owner)
        if not target or target['team'] != settings.slack_team_id:
            raise HTTPException(404, 'Choose an enrolled person in this workspace.')
        if not await slack.verify_member(body.owner, fresh=True):
            raise HTTPException(403, 'That Slack account is no longer eligible.')
        async with deployment.lock:
            await admin_account(request)
            deployment.save(config={'slack_owner_user_id': body.owner.split(':', 1)[1]})
        return {'status': 'transferred'}

    @api.put('/admin/sharing')
    async def workspace_sharing(request: Request):
        await admin_account(request)
        body = await request.json()
        enabled = body.get('enabled')
        if not isinstance(enabled, bool):
            raise HTTPException(422, 'Choose whether company sharing is enabled.')
        deployment.save(config={'sharing_enabled': enabled})
        archive.sharing_enabled = enabled
        await archive.db.execute('UPDATE members SET policy_version=policy_version+1')
        if not enabled:
            # Re-enabling later does not resurrect previously published material.
            await archive.db.execute("UPDATE records SET visibility='private',intent='private'")
        await archive.db.commit()
        return {'sharing_enabled': enabled}

    @api.put("/admin/models")
    async def save_models(body: SaveModels, request: Request):
        await admin_account(request)
        return await model_service.save(body.models, expected_revision=body.revision)

    @api.post("/admin/models/check-jev")
    async def check_jev(request: Request):
        await admin_account(request)
        return await gateway.probe_jev()

    @api.get("/people")
    async def people(request: Request):
        return {"people": await archive.people(Reader(await account(request)))}

    @api.post("/people/{owner}/overview")
    async def overview(owner: str, body: ViewRequest, request: Request):
        same_origin(request)
        return await views.get(Reader(await account(request)), owner, body)

    @api.post("/memories")
    async def capture(body: WorkInput, request: Request):
        same_origin(request)
        return await archive.capture(await account(request), body)

    @api.post("/memories/search")
    async def search(body: Search, request: Request):
        same_origin(request)
        reader = Reader(await account(request))
        start = date_boundary(body.created_from, body.date_timezone)
        end = date_boundary(body.created_before, body.date_timezone)
        if start and end and start >= end:
            raise HTTPException(422, "The end date must follow the start date.")
        vector = None
        if body.query.strip() and body.mode == "hybrid":
            with contextlib.suppress(Exception):
                vector = (await gateway.embeddings([body.query]))[0]
        filters = body.model_dump(exclude={"mode", "date_timezone"})
        filters.update(created_from=start.isoformat() if start else None,
                       created_before=end.isoformat() if end else None)
        return await archive.search(reader, **filters, vector=vector, embedding_model=settings.memory_embedding_model)

    @api.get("/memories/{record_id}")
    async def read(record_id: str, request: Request):
        record = await archive.read(Reader(await account(request)), record_id)
        return {k: v for k, v in record.items() if k not in {"embedding", "publication_embedding"}}

    @api.put("/memories/{record_id}/visibility")
    async def visibility(record_id: str, request: Request):
        owner = await browser_account(request)
        body = await request.json()
        await archive.set_visibility(owner, record_id, body.get("visibility"))
        return {"status": "saved"}

    @api.get('/memories/{record_id}/sharing-preview')
    async def sharing_preview(record_id: str, request: Request):
        owner = await browser_account(request)
        row = await archive.row(record_id)
        if not row or row['owner'] != owner or row['deleted']:
            raise LookupError('Memory not found')
        payload = await archive.decode(row)
        eligible = await archive.publication_allowed({**row, 'intent': 'public'}, payload)
        can_share = (row['status'] == 'ready' and eligible and bool(payload.get('summary'))
                     and payload.get('privacy_blocked') is False)
        return {'title': payload['title'], 'summary': payload.get('summary', ''), 'can_share': can_share,
                'notice': 'Only this summary is shared with eligible company members. Original evidence stays private.'
                if can_share else 'This memory is still processing, matches a private topic, or is excluded by your sharing types.'}

    @api.delete("/memories/{record_id}")
    async def delete(record_id: str, request: Request):
        await archive.delete(await browser_account(request), record_id)
        return {"status": "deleted"}

    @api.post('/memories/{record_id}/corrections')
    async def correction(record_id: str, body: Correction, request: Request):
        return await archive.correct(await browser_account(request), record_id, **body.model_dump())

    @api.get("/privacy-rules")
    async def rules(request: Request):
        return {"rules": await archive.rules(await account(request))}

    @api.get('/view-preferences')
    async def view_preferences(request: Request):
        return await archive.view_preferences(await account(request))

    @api.get('/display-preferences')
    async def display_preferences(request: Request):
        return await archive.display_preferences(await account(request))

    @api.put('/display-preferences')
    async def save_display_preferences(body: DisplayPreferences, request: Request):
        return await archive.save_display_preferences(await browser_account(request), body)

    @api.get('/schedule')
    async def schedule(request: Request):
        await account(request)
        return views.schedule_status()

    @api.put('/admin/schedule')
    async def save_schedule(body: ScheduleUpdate, request: Request):
        await admin_account(request)
        return await views.save_schedule(body)

    @api.put('/view-preferences')
    async def save_view_preferences(body: ViewPreferences, request: Request):
        return await archive.save_view_preferences(await browser_account(request), body)

    @api.get("/sharing-preferences")
    async def sharing_preferences(request: Request):
        return await archive.sharing_preferences(await account(request))

    @api.put("/sharing-preferences")
    async def save_sharing_preferences(request: Request):
        owner = await browser_account(request)
        body = await request.json()
        previous = await archive.sharing_preferences(owner)
        return await archive.set_sharing_preferences(
            owner, body.get('allowed_types', previous['allowed_types']), body.get('auto_share'))

    @api.post("/privacy-rules")
    async def add_rule(request: Request):
        owner = await browser_account(request)
        body = await request.json()
        return await archive.add_rule(owner, str(body.get("instruction", "")))

    @api.delete("/privacy-rules/{rule_id}")
    async def remove_rule(rule_id: str, request: Request):
        await archive.remove_rule(await browser_account(request), rule_id)
        return {"status": "removed"}

    @api.put("/preferences")
    async def preferences(request: Request):
        owner = await browser_account(request)
        body = await request.json()
        for key in ["capture_enabled", "agent_enabled"]:
            if key in body and not isinstance(body[key], bool):
                raise HTTPException(422, "Use true or false for this setting.")
        await archive.preferences(owner, **{k: body[k] for k in ["capture_enabled", "agent_enabled"] if k in body})
        return {"status": "saved"}

    @api.post("/retry-processing")
    async def retry(request: Request):
        await archive.retry_failed(await browser_account(request))
        return {"status": "queued"}

    @api.get("/connections")
    async def connections(request: Request):
        return {"connections": await archive.tokens(await account(request))}

    @api.get("/capture-identity")
    async def capture_identity(request: Request):
        owner = await account(request)
        member = await archive.member(owner)
        return {"owner": owner, "capture_enabled": bool(member['capture_enabled']),
                "capture_after": member['capture_after']}

    @api.get("/connector/setup", response_class=HTMLResponse)
    async def connector_setup(request: Request):
        await browser_account(request)
        url = escape(settings.public_base_url.rstrip("/"))
        return HTMLResponse(f'''<!doctype html><html lang="en"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"><title>Connect your tools · litemem</title>
        <link rel="stylesheet" href="/static/memory.css"><main class="auth-card guide-page">
        <a href="/#connections">← Connections</a><p class="eyebrow">Connect a tool</p><h1>Record as you work</h1>
        <p>Install the connector once on the computer where you use Codex or Claude Code.
        Sign in with Slack when the browser opens. You don't need an API key or a memory password.</p>
        <h2>1. Install &amp; connect</h2>
        <pre class="code-block">uv tool install git+https://github.com/moe-berri/litemem.git@codex/litemem-implementation
litemem-connector connect {url}</pre>
        <h2>2. Restart your coding tool</h2>
        <p>The installer adds capture hooks and the litemem MCP, preserving your existing connections.
        Restart Codex or Claude Code, then sign in to its litemem MCP when prompted.
        In Codex, open <code>/hooks</code> and review/trust the litemem capture hooks.</p>
        <p>Visible conversation text is recorded privately. Tool outputs, system messages and hidden reasoning
        are excluded. Memories are searched only when you ask.</p>
        <details class="advanced"><summary>Choose a tool or import history</summary>
        <pre class="code-block">litemem-connector connect {url} --host codex
litemem-connector connect {url} --host claude
litemem-connector backfill ~/.codex/sessions --host codex
litemem-connector backfill ~/.claude/projects --host claude</pre>
        <p>History import is opt-in. Review which projects you want to import first; a specific folder or
        transcript file works too. Originals and their dates are preserved.</p></details>
        <details class="advanced"><summary>Status, pause, and disconnect</summary>
        <pre class="code-block">litemem-connector status
litemem-connector flush
litemem-connector pause
litemem-connector resume
litemem-connector disconnect</pre>
        <p>The connector buffers offline work locally with encryption and retries on the next hook.
        A 100 MB limit pauses buffering instead of silently dropping old work. The status command reports
        queued work and items needing attention. Revoke access at any time from Connections.</p></details>
        <p class="fine-print">Requires Python 3.12+, uv, and a current Codex or Claude Code on macOS/Linux.
        Transcript formats can change; check capture status after a host update.
        While this repository is private, installation also requires GitHub access.</p></main></html>''')

    @api.post("/connections")
    async def connection(request: Request):
        return await archive.create_token(await browser_account(request))

    @api.delete("/connections/{connection_id}")
    async def revoke(connection_id: str, request: Request):
        await archive.revoke_token(await browser_account(request), connection_id)
        return {"status": "revoked"}

    @api.post("/export")
    async def export(request: Request, body: ExportOptions | None = None):
        same_origin(request)
        owner = await account(request)
        options = body or ExportOptions()
        async with export_slots:
            stream = await asyncio.to_thread(ExportStream, options.passphrase, owner) if options.encrypted else None
        async def records():
            if stream:
                yield json.dumps(stream.header) + '\n'
            async for record in archive.iter_records(Reader(owner), owner=owner):
                value = {k: v for k, v in record.items() if k not in {'embedding', 'publication_embedding'}}
                yield stream.line(value) if stream else json.dumps(value, ensure_ascii=False) + '\n'
            if stream:
                yield stream.finish()
        extension = 'litemem' if stream else 'jsonl'
        return StreamingResponse(records(), media_type='application/octet-stream' if stream else 'application/x-ndjson',
            headers={'Content-Disposition': f'attachment; filename="my-memory-export.{extension}"', 'Cache-Control': 'no-store'})

    @app.get('/transfer/receive')
    async def transfer_page():
        return FileResponse(ROOT / 'static/transfer.html')

    @app.get('/transfer/send')
    async def transfer_send_page():
        return FileResponse(ROOT / 'static/transfer-send.html')

    @api.get('/transfers')
    async def recent_transfers(request: Request):
        return {'transfers': await transfers.recent(await browser_account(request))}

    @api.post('/transfers/receive')
    async def receive_transfer(body: ReceiveRequest, request: Request):
        return await transfers.receive(await browser_account(request), body.source)

    @api.post('/transfers/send')
    async def send_transfer(body: Invitation, request: Request):
        return await transfers.outgoing(await browser_account(request), body)

    @api.get('/transfers/{identity}')
    async def transfer_status(identity: UUID, request: Request):
        return await transfers.status(identity, await browser_account(request))

    @api.get('/transfers/{identity}/packets/{index}')
    async def transfer_packet(identity: UUID, index: int, request: Request):
        return await transfers.send_packet(identity, await browser_account(request), index)

    @api.get('/transfers/{identity}/outgoing')
    async def transfer_context(identity: UUID, request: Request):
        return await transfers.outgoing_context(identity, await browser_account(request))

    @api.post('/transfers/{identity}/packets')
    async def transfer_upload(identity: UUID, body: Packet, request: Request):
        return await transfers.accept(identity, await browser_account(request), body)

    @api.post('/transfers/{identity}/finish', status_code=202)
    async def transfer_finish(identity: UUID, request: Request):
        return await transfers.start_import(identity, await browser_account(request))

    @api.delete('/transfers/{identity}')
    async def transfer_cancel(identity: UUID, request: Request):
        return await transfers.cancel(identity, await browser_account(request))

    @api.get('/transfer-inbox/{identity}')
    @api.post('/transfer-inbox/{identity}')
    @api.delete('/transfer-inbox/{identity}')
    @api.options('/transfer-inbox/{identity}')
    async def transfer_inbox(identity: UUID, request: Request):
        token = request.headers.get('authorization', '')
        if request.method != 'OPTIONS' and not token.startswith('Bearer '):
            raise HTTPException(403, 'A receiving grant is required.')
        row = await transfers.inbox(identity, request.headers.get('origin'),
                                    None if request.method == 'OPTIONS' else token[7:])
        cors = {'Access-Control-Allow-Origin': row['payload']['invitation']['source'], 'Vary': 'Origin',
                'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': 'Authorization, Content-Type', 'Cache-Control': 'no-store'}
        if request.method == 'OPTIONS':
            return JSONResponse({}, headers=cors)
        try:
            if request.method == 'GET':
                result = transfers.public(row)
            elif request.method == 'DELETE':
                result = await transfers.cancel(identity, row['owner'])
            else:
                body = await request.json()
                if body.get('action') == 'packet':
                    result = await transfers.accept(identity, row['owner'], Packet.model_validate(body.get('packet')))
                elif body.get('action') == 'finish':
                    result = await transfers.start_import(identity, row['owner'])
                else:
                    raise ValueError('Choose a supported transfer action.')
            return JSONResponse(result, headers=cors)
        except (ValueError, KeyError, TypeError, AttributeError):
            return JSONResponse({'detail': 'Transfer verification failed. Reconnect from the source and retry.'},
                                status_code=400, headers=cors)

    @api.post("/leave")
    async def leave(request: Request):
        await archive.leave(await browser_account(request))
        request.session.clear()
        return {"status": "left"}

    @api.post("/chat", status_code=202)
    async def chat(body: Question, request: Request):
        same_origin(request)
        owner = await account(request)
        if not body.question.strip():
            raise HTTPException(422, "Enter a question.")
        await archive.key(owner, require_agent=True)
        chosen = await gateway.validate_model(body.model or (await model_service.current()).agent_model)
        payload = body.model_dump(mode="json") | {"principal": owner, "model": chosen,
                                                  "request_source": "api" if request.headers.get("authorization", "").startswith("Bearer ") else "web",
                                                  "model_config_revision": settings.model_config_revision}
        payload["policy_stamp"] = await archive.policy_stamp(Reader(owner, body.audience))
        payload["history"] = []
        if body.previous_job_id:
            previous = await authorized_job(request, str(body.previous_job_id))
            previous_payload = json.loads(previous["payload"])
            if (previous["status"] != "done" or previous_payload.get("scope") != body.scope
                    or previous_payload.get("audience", "owner") != body.audience):
                raise HTTPException(409, "Start a new conversation after changing its memory scope.")
            payload["history"] = (previous_payload.get("history", []) + [
                {"role": "user", "content": previous_payload["question"]},
                {"role": "assistant", "content": previous["answer"] or ""},
            ])[-16:]
        job_id = await store.enqueue(owner, "web", payload, f"web:{owner}:{body.request_id}" if body.request_id else None)
        return {"id": job_id, "status": "queued"}

    async def authorized_job(request, job_id):
        owner = await account(request)
        job = await store.get_job(job_id)
        if not job or job["owner"] != owner or job["kind"] != "web":
            raise HTTPException(404, "Request not found")
        payload = json.loads(job["payload"])
        if payload.get("policy_stamp") != await archive.policy_stamp(Reader(owner, payload.get("audience", "owner"))):
            raise HTTPException(409, "Access changed. Start a new conversation.")
        return job

    @api.get("/jobs/{job_id}")
    async def job(job_id: str, request: Request):
        row = await authorized_job(request, job_id)
        return {k: row[k] for k in ["id", "status", "answer", "partial", "phase", "error"]}

    @api.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        same_origin(request)
        await authorized_job(request, job_id)
        await store.cancel(job_id)
        if job_id in running:
            running[job_id].cancel()
        return {"status": "cancelled"}

    @app.get("/auth/slack")
    async def login(request: Request):
        if not settings.slack_client_id:
            raise HTTPException(503, "Configure Slack sign-in to use this installation.")
        request.session["login_next"] = safe_return(request.query_params.get("next", "/"))
        return await oauth.slack.authorize_redirect(request, settings.public_base_url + "/auth/slack/callback",
                                                    team=settings.slack_team_id, response_mode="form_post")

    @app.get("/auth/slack/callback")
    @app.post("/auth/slack/callback")
    async def login_callback(request: Request):
        target = safe_return(request.session.get("login_next", "/"))
        setup_grant = request.session.get("setup_grant") if deployment.authorized(request) else None
        try:
            token = await oauth.slack.authorize_access_token(request)
            info = token["userinfo"]
            identity = {"user_id": info.get("https://slack.com/user_id") or info["sub"],
                        "team_id": info["https://slack.com/team_id"], "email": info.get("email", ""),
                        "email_verified": info.get("email_verified") is True,
                        "name": info.get("name", "Member"), "avatar": info.get("picture", "")}
            request.session.clear()
            request.session["identity"] = identity
            if setup_grant:
                request.session["setup_grant"] = setup_grant
            session_identity(request)
            owner = f"{identity['team_id']}:{identity['user_id']}"
            if museum and await archive.member(owner):
                await archive.enroll(owner, {k: identity[k] for k in ["name", "avatar", "email"]})
                slack.settings.allowed_slack_users = ",".join(p["id"] for p in await archive.people(Reader(owner)))
        except Exception:
            request.session.clear()
            if setup_grant:
                request.session["setup_grant"] = setup_grant
            return HTMLResponse('<!doctype html><title>Sign-in incomplete</title><link rel="stylesheet" href="/static/memory.css">'
                                '<main class="auth-card"><h1>Sign-in wasn’t completed</h1>'
                                '<p>Use your eligible company Slack account.</p>'
                                '<a href="/">Return to litemem</a></main>', status_code=403)
        return RedirectResponse(target, status_code=303)

    @app.post("/auth/logout")
    async def logout(request: Request):
        same_origin(request)
        request.session.clear()
        return {"ok": True}

    @app.post("/slack/events")
    async def events(request: Request):
        raw = await request.body()
        # Slack verifies the manifest callback while the app is being created,
        # before its signing secret exists. This bootstrap-only echo grants no
        # event handling or memory access and closes once credentials are saved.
        if not settings.slack_signing_secret and not deployment.complete:
            envelope = json.loads(raw)
            challenge = envelope.get("challenge")
            if envelope.get("type") == "url_verification" and isinstance(challenge, str) and len(challenge) <= 256:
                return {"challenge": challenge}
        if not settings.slack_signing_secret or not SignatureVerifier(settings.slack_signing_secret).is_valid_request(
            raw, dict(request.headers)
        ):
            raise HTTPException(401, "Invalid Slack signature")
        envelope = json.loads(raw)
        if envelope.get("type") == "url_verification":
            return {"challenge": envelope["challenge"]}
        if envelope.get("type") == "event_callback" and envelope.get("event_id"):
            event = envelope.get('event', {})
            if event.get('type') == 'user_change' and envelope.get('team_id') == settings.slack_team_id:
                user = event.get('user', {})
                user_id = user.get('id')
                if user_id:
                    owner = settings.slack_team_id + ':' + user_id
                    slack.member_checks.pop(owner, None)
                    if slack.explicitly_ineligible(user) and await archive.member(owner):
                        await archive.leave(owner)
                return {'ok': True}
            await slack.event(envelope)
        return {"ok": True}

    @app.get("/slack/install/callback")
    async def install(request: Request, code: str = "", state: str = ""):
        try:
            signed = URLSafeTimedSerializer(settings.session_secret, salt="slack-install").loads(state, max_age=3600)
            setup_install = isinstance(signed, dict) and "setup" in signed
            if setup_install:
                deployment.require(request)
                nonce = hashlib.sha256(signed["setup"].encode()).hexdigest()
                if not secrets.compare_digest(nonce, deployment.values.get("install_nonce", "")):
                    raise ValueError()
                deployment.save(install_nonce="")
            elif signed != settings.slack_team_id:
                raise ValueError()
            if not code:
                raise ValueError()
        except (BadSignature, ValueError):
            raise HTTPException(400, "Installation link expired") from None
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://slack.com/api/oauth.v2.access", data={
                "client_id": settings.slack_client_id, "client_secret": settings.slack_client_secret, "code": code,
                "redirect_uri": settings.public_base_url + "/slack/install/callback"})
        data = response.json()
        team_id, owner_id = data.get("team", {}).get("id"), data.get("authed_user", {}).get("id")
        if (not data.get("ok") or not team_id or not owner_id
                or (settings.slack_team_id and team_id != settings.slack_team_id)
                or (settings.slack_owner_user_id and owner_id != settings.slack_owner_user_id)):
            raise HTTPException(403, "Only the deployment owner can install the Slack app.")
        if setup_install:
            deployment.save(config={"slack_team_id": team_id, "slack_owner_user_id": owner_id})
            app.state.update_slack_config()
        await store.setting("slack_bot_token", data["access_token"])
        await store.setting("slack_bot_user_id", data["bot_user_id"])
        await store.setting("slack_scopes", data.get("scope", ""))
        return RedirectResponse("/", status_code=303)

    @api.get("/health")
    async def private_health(request: Request):
        owner = await account(request)
        return {"storage": "ente" if museum else "unconfigured", "stats": await archive.stats(owner),
                "sharing_enabled": settings.sharing_enabled, "background_model": settings.background_model,
                "classifier": settings.jev_model}

    # One router keeps versioned and compatibility requests on identical handlers.
    app.include_router(api, prefix="/api/v1")
    app.include_router(api, prefix="/api", include_in_schema=False)
    app.mount("/mcp", mcp_http)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.add_middleware(RequestGuard)
    return app


app = create_app()
