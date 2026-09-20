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
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field


class ClientRegistration(BaseModel):
    client_name: str = Field(default="Memory client", max_length=100)
    redirect_uris: list[str] = Field(min_length=1, max_length=8)
    token_endpoint_auth_method: str = "none"


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
                "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"], "scopes_supported": ["memory"]}

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def resource_metadata():
        return {"resource": resource, "authorization_servers": [issuer], "scopes_supported": ["memory"],
                "bearer_methods_supported": ["header"], "resource_name": "litemem"}

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
        if (p.get("redirect_uri") not in client["redirect_uris"] or p.get("response_type") != "code"
                or p.get("code_challenge_method") != "S256" or len(p.get("code_challenge", "")) != 43
                or p.get("resource", resource) != resource or p.get("scope", "memory") != "memory"):
            raise HTTPException(400, "Invalid authorization request")
        try:
            await account(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                return RedirectResponse("/auth/slack?" + urlencode({"next": request.url.path + "?" + request.url.query}),
                                        status_code=303)
            raise
        nonce = secrets.token_urlsafe(32)
        request.session["oauth_pending"] = {**p, "nonce": nonce, "created": time.time()}
        client_name = escape(client["client_name"])
        return HTMLResponse(f'''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width">
        <title>Connect to litemem</title><link rel="stylesheet" href="/static/memory.css">
        <main class="auth-card"><p class="eyebrow">litemem</p><h1>Connect {client_name}</h1>
        <p>This client can record your work and search your private and shared memories.</p>
        <p>You can revoke access from Connections at any time. It cannot change sharing settings.</p>
        <form method="post" action="/oauth/authorize"><input type="hidden" name="nonce" value="{nonce}">
        <button class="button primary" type="submit">Connect</button> <a href="/">Cancel</a></form></main></html>''',
                            headers={"Cache-Control": "no-store"})

    @app.post("/oauth/authorize")
    async def approve(request: Request):
        same_origin(request)
        owner = await account(request)
        p = request.session.pop("oauth_pending", None)
        form = await request.form()
        if not p or time.time() - p["created"] > 600 or not secrets.compare_digest(p["nonce"], str(form.get("nonce", ""))):
            raise HTTPException(400, "Connection request expired. Start again from your client.")
        code = secrets.token_urlsafe(40)
        digest = hashlib.sha256(code.encode()).hexdigest()
        await store.setting("oauth-code:" + digest, json.dumps({**p, "owner": owner}))
        params = {"code": code}
        if p.get("state"):
            params["state"] = p["state"]
        sep = "&" if "?" in p["redirect_uri"] else "?"
        return RedirectResponse(p["redirect_uri"] + sep + urlencode(params), status_code=303,
                                headers={"Cache-Control": "no-store"})

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

    @app.post("/oauth/token")
    async def token(request: Request):
        p = dict(await request.form())
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
