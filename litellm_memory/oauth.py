"""OAuth authorization-code + PKCE for personal MCP connections.

Slack authenticates the human; the MCP client stores its own revocable bearer
credential. No vault password or recovery phrase is required.
"""

import base64
import hashlib
import json
import secrets
import time
from html import escape
from typing import Literal
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field


class ReminderHook(BaseModel):
    host: Literal['codex', 'claude']
    command: str = Field(min_length=1, max_length=4000)


class ClientRegistration(BaseModel):
    client_name: str = Field(default="Memory client", max_length=100)
    redirect_uris: list[str] = Field(min_length=1, max_length=8)
    token_endpoint_auth_method: str = "none"
    reminder_hooks: list[ReminderHook] = Field(default_factory=list, max_length=2)


def valid_redirect(url):
    try:
        p = urlsplit(url)
        return (not p.fragment and not p.username and not p.password and bool(p.hostname)
                and (p.scheme == "https" or (p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"})))
    except ValueError:
        return False


def install_oauth(app, settings, store, archive, account, same_origin):
    issuer = settings.public_base_url.rstrip("/")
    resource = issuer + "/mcp"

    @app.get("/.well-known/oauth-authorization-server")
    async def metadata():
        return {"issuer": issuer, "authorization_endpoint": issuer + "/oauth/authorize",
                "token_endpoint": issuer + "/oauth/token", "registration_endpoint": issuer + "/oauth/register",
                "revocation_endpoint": issuer + "/oauth/revoke", "response_types_supported": ["code"],
                "device_authorization_endpoint": issuer + "/oauth/device/code",
                "grant_types_supported": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:device_code"], "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"], "scopes_supported": ["memory"]}

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def resource_metadata():
        return {"resource": resource, "authorization_servers": [issuer], "scopes_supported": ["memory"],
                "bearer_methods_supported": ["header"], "resource_name": "LiteLLM Memory"}

    @app.post("/oauth/register", status_code=201)
    async def register(body: ClientRegistration):
        if body.token_endpoint_auth_method != "none" or any(not valid_redirect(u) for u in body.redirect_uris):
            raise HTTPException(400, "Use a registered HTTPS or loopback callback and PKCE.")
        client_id = secrets.token_urlsafe(24)
        await store.setting("oauth-client:" + client_id, json.dumps(body.model_dump()))
        return {"client_id": client_id, **body.model_dump(), "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]}

    @app.get("/oauth/authorize", response_class=HTMLResponse)
    async def authorize(request: Request):
        p = dict(request.query_params)
        client = await store.setting("oauth-client:" + p.get("client_id", ""))
        if not client:
            raise HTTPException(400, "Unknown client")
        client = json.loads(client)
        if (p.get("redirect_uri") not in client.get("redirect_uris", []) or p.get("response_type") != "code"
                or p.get("code_challenge_method") != "S256" or len(p.get("code_challenge", "")) != 43
                or p.get("resource", resource) != resource or p.get("scope", "memory") != "memory"):
            raise HTTPException(400, "Invalid authorization request")
        try:
            owner = await account(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                if request.session.get("identity"):
                    # A new Slack member must finish account setup once, then
                    # return to this request instead of repeating Slack login.
                    return RedirectResponse("/?" + urlencode({"connect": request.url.query}), status_code=303)
                return RedirectResponse("/auth/slack?" + urlencode({"next": request.url.path + "?" + request.url.query}),
                                        status_code=303)
            raise
        nonce = secrets.token_urlsafe(32)
        # Consent lives outside the session cookie: another tab or a delayed
        # response must not overwrite it. Each request is single-use and bound
        # to the member who saw this client's permissions.
        await store.create_oauth_request(hashlib.sha256(nonce.encode()).hexdigest(),
                                         {**p, "owner": owner}, time.time() + 600)
        client_name = escape(client["client_name"])
        reminders = ''
        if client.get('reminder_hooks'):
            commands = ''.join('<p>' + ('Codex' if hook['host'] == 'codex' else 'Claude Code')
                               + '</p><pre class="code-block">' + escape(hook['command']) + '</pre>'
                               for hook in client['reminder_hooks'])
            reminders = ('<p>Connecting also allows this installer to set up your coding tools and approve '
                         'reminders to save useful work. These hooks run when a session starts and when you send '
                         'a message. They do not read or upload transcripts.</p>'
                         '<details class="advanced"><summary>Review reminder commands</summary>' + commands
                         + '</details>')
        return HTMLResponse(f'''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width">
        <title>Connect to LiteLLM Memory</title><link rel="stylesheet" href="/static/memory.css">
        <main class="auth-card"><p class="eyebrow">LiteLLM Memory</p><h1>Connect {client_name}</h1>
        <p>This client can record your work and search your private and shared memories.</p>
        <p>You can revoke access from Connections at any time. It cannot change sharing settings.</p>
        {reminders}
        <form method="post" action="/oauth/authorize"><input type="hidden" name="nonce" value="{nonce}">
        <button class="button primary" type="submit">Connect</button> <a href="/">Cancel</a></form></main></html>''',
                            headers={"Cache-Control": "no-store"})

    @app.post("/oauth/authorize")
    async def approve(request: Request):
        same_origin(request)
        owner = await account(request)
        form = await request.form()
        nonce = str(form.get("nonce", ""))
        p = await store.consume_oauth_request(hashlib.sha256(nonce.encode()).hexdigest(), owner)
        if not p or "device" in p:
            return HTMLResponse('''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width">
            <title>Start a new connection · LiteLLM Memory</title><link rel="stylesheet" href="/static/memory.css">
            <main class="auth-card"><p class="eyebrow">LiteLLM Memory</p><h1>Start a new connection</h1>
            <p>This connection request is no longer available for this account.</p>
            <p>Return to your coding tool or terminal and start connecting again. Use the new page it opens.</p>
            <a class="button primary" href="/#connections">Open Connections</a></main></html>''',
                                status_code=400, headers={"Cache-Control": "no-store"})
        code = secrets.token_urlsafe(40)
        digest = hashlib.sha256(code.encode()).hexdigest()
        await store.setting("oauth-code:" + digest, json.dumps({**p, "created": time.time()}))
        params = {"code": code}
        if p.get("state"):
            params["state"] = p["state"]
        sep = "&" if "?" in p["redirect_uri"] else "?"
        callback = escape(p["redirect_uri"] + sep + urlencode(params))
        # A cross-origin redirect after a form POST is blocked by form-action
        # in Chromium. Finish the POST here, then navigate from this document.
        # Keep the application's CSP unchanged and offer a no-JavaScript link.
        return HTMLResponse(f'''<!doctype html><html lang="en"><head>
        <meta charset="utf-8"><meta name="viewport" content="width=device-width">
        <title>Return to your client · LiteLLM Memory</title>
        <link rel="stylesheet" href="/static/memory.css">
        <script src="/static/oauth-callback.js" defer></script></head><body>
        <main class="auth-card"><p class="eyebrow">LiteLLM Memory</p><h1>Return to your client</h1>
        <p>Your approval is ready. Your client will open to finish connecting.</p>
        <a id="client-callback" class="button primary" href="{callback}" rel="noreferrer">Return to client</a>
        <p class="fine-print">If your client is no longer waiting, return to your coding tool or terminal
        and start connecting again.</p></main></body></html>''', headers={"Cache-Control": "no-store"})

    async def issue_refresh(connection_id, client_id):
        value = secrets.token_urlsafe(48)
        await archive.db.execute("INSERT INTO refresh_tokens(digest,connection_id,client_id,expires) VALUES(?,?,?,?)",
                                 (hashlib.sha256(value.encode()).hexdigest(), connection_id, client_id,
                                  time.time() + 86400 * 30))
        await archive.db.commit()
        return value

    async def rotate_refresh(p):
        if p.get("resource", resource) != resource:
            raise HTTPException(400, "invalid_grant")
        digest = hashlib.sha256(p.get("refresh_token", "").encode()).hexdigest()
        async with app.state.oauth_lock:
            row = await (await archive.db.execute("""SELECT r.*,t.owner,t.revoked FROM refresh_tokens r
                JOIN api_tokens t ON t.id=r.connection_id WHERE r.digest=?""", (digest,))).fetchone()
            if not row or row["client_id"] != p.get("client_id"):
                raise HTTPException(400, "invalid_grant")
            if row["consumed"]:
                # Reuse of a rotated token revokes this connection family.
                await archive.revoke_token(row["owner"], row["connection_id"])
                raise HTTPException(400, "invalid_grant")
            if row["revoked"] or row["expires"] <= time.time() or not await archive.member(row["owner"]):
                raise HTTPException(400, "invalid_grant")
            value = "llmem_" + secrets.token_urlsafe(40)
            await archive.db.execute("UPDATE refresh_tokens SET consumed=1 WHERE digest=?", (digest,))
            await archive.db.execute("UPDATE api_tokens SET digest=?,expires=? WHERE id=?", (
                hashlib.sha256(value.encode()).hexdigest(), time.time() + 3600, row["connection_id"]))
            refresh = await issue_refresh(row["connection_id"], row["client_id"])
        return token_response(value, refresh)

    def token_response(value, refresh):
        from fastapi.responses import JSONResponse
        return JSONResponse({"access_token": value, "token_type": "Bearer", "scope": "memory",
                             "expires_in": 3600, "refresh_token": refresh},
                            headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    from .device_oauth import install_device_oauth
    redeem_device = install_device_oauth(app, store, archive, account, same_origin, issuer, issue_refresh, token_response)

    @app.post("/oauth/token")
    async def token(request: Request):
        p = dict(await request.form())
        if p.get("grant_type") == "urn:ietf:params:oauth:grant-type:device_code":
            return await redeem_device(p)
        if p.get("grant_type") == "refresh_token":
            return await rotate_refresh(p)
        if p.get("grant_type") != "authorization_code":
            raise HTTPException(400, "unsupported_grant_type")
        digest = hashlib.sha256(p.get("code", "").encode()).hexdigest()
        # Serialize code consumption so simultaneous exchanges cannot replay it.
        async with app.state.oauth_lock:
            raw = await store.setting("oauth-code:" + digest)
            if not raw or raw == "used":
                raise HTTPException(400, "invalid_grant")
            grant = json.loads(raw)
            verifier = p.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if (time.time() - grant["created"] > 600 or not 43 <= len(verifier) <= 128
                    or not secrets.compare_digest(challenge, grant["code_challenge"])
                    or p.get("redirect_uri") != grant["redirect_uri"] or p.get("client_id") != grant["client_id"]
                    or p.get("resource", resource) != resource):
                raise HTTPException(400, "invalid_grant")
            client = json.loads(await store.setting("oauth-client:" + grant["client_id"]))
            # Consume before minting: a crash may require a fresh login, but can
            # never turn one authorization code into multiple connections.
            await store.setting("oauth-code:" + digest, "used")
            created = await archive.create_token(grant["owner"], client["client_name"], lifetime=3600)
            refresh = await issue_refresh(created["id"], grant["client_id"])
        return token_response(created["token"], refresh)

    @app.post("/oauth/revoke")
    async def revoke(request: Request):
        p = dict(await request.form())
        digest = hashlib.sha256(p.get("token", "").encode()).hexdigest()
        await archive.db.execute("""UPDATE api_tokens SET revoked=1 WHERE digest=? OR id IN
            (SELECT connection_id FROM refresh_tokens WHERE digest=?)""", (digest, digest))
        await archive.db.commit()
        return {}
