"""One progressive, resumable installation flow, claimed by its deployment owner."""

import asyncio
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from itsdangerous import URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .archive import Reader
from .crypto import decrypt_blob, encrypt_blob, new_key
from .gateway import Gateway
from .model_settings import ModelConfiguration, ModelSettingsService
from .slack_manifest import BOT_SCOPES, slack_manifest


class DeploymentConfig:
    """Encrypted boot config is available before the HTTP clients are constructed."""
    def __init__(self, settings, vault):
        self.settings, self.vault = settings, vault
        self.path = settings.state_dir / "deployment.enc"
        self.claim_path = settings.state_dir / "setup-code"
        self.lock = asyncio.Lock()
        self.values = vault.open_operational(self.path.read_text()) if self.path.exists() else {}
        for field, value in self.values.get("settings", {}).items():
            if field in type(settings).model_fields:
                setattr(settings, field, value)
        if not self.complete and not self.claim_path.exists():
            self._atomic(self.claim_path, settings.setup_code or secrets.token_urlsafe(32))

    @property
    def complete(self):
        return bool(self.values.get("complete"))

    def _atomic(self, path: Path, text):
        temp = path.with_name(path.name + "." + secrets.token_hex(8))
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def save(self, *, config=None, **values):
        self.values.update(values)
        if config:
            self.values.setdefault("settings", {}).update(config)
        self._atomic(self.path, self.vault.seal_operational(self.values))
        if config:
            for field, value in config.items():
                setattr(self.settings, field, value)

    def authorized(self, request):
        grant = request.session.get("setup_grant", "")
        return bool(not self.complete and grant and time.time() < self.values.get("claim_expires", 0)
                    and secrets.compare_digest(hashlib.sha256(grant.encode()).hexdigest(),
                                               self.values.get("claim_digest", "")))

    def require(self, request):
        if not self.authorized(request):
            raise HTTPException(403, "Claim this installation with its deployment setup code first.")


class GatewayConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=2000)
    api_key: str = Field(default="", max_length=8000)

    @field_validator("base_url")
    @classmethod
    def url(cls, value):
        value = value.strip().rstrip("/")
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError("Use the base URL of your LiteLLM gateway, without credentials or query parameters.")
        return value if value.endswith("/v1") else value + "/v1"


class SlackConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(pattern=r"^A[A-Z0-9]{5,30}$")
    client_id: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    client_secret: str = Field(min_length=20, max_length=300)
    signing_secret: str = Field(min_length=20, max_length=300)


class SetupModels(BaseModel):
    models: ModelConfiguration
    revision: int = Field(ge=0)


class FinishSetup(BaseModel):
    allowed_email_domain: str = Field(default="berri.ai", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    timezone: str = Field(default="UTC", max_length=100)


def install_setup(app, api, deployment, settings, store, archive, gateway, model_service, slack, oauth, same_origin):
    def require(request):
        same_origin(request)
        deployment.require(request)

    def update_slack():
        for field in ["slack_app_id", "slack_client_id", "slack_client_secret", "slack_signing_secret",
                      "slack_team_id", "slack_owner_user_id", "allowed_email_domain"]:
            setattr(slack.settings, field, getattr(settings, field))
        oauth.register("slack", overwrite=True, client_id=settings.slack_client_id,
                       client_secret=settings.slack_client_secret,
                       server_metadata_url="https://slack.com/.well-known/openid-configuration",
                       client_kwargs={"scope": "openid profile email"})

    app.state.update_slack_config = update_slack

    @api.get("/setup/status")
    async def status(request: Request):
        result = {"required": not deployment.complete, "claimed": deployment.authorized(request)}
        if result["claimed"]:
            identity = request.session.get('identity', {})
            verified = bool(identity.get('team_id') == settings.slack_team_id
                            and identity.get('user_id') == settings.slack_owner_user_id
                            and identity.get('email_verified') is True
                            and identity.get('email', '').casefold().rsplit('@', 1)[-1]
                            == settings.allowed_email_domain.casefold())
            result.update(base_url=settings.litellm_base_url if settings.litellm_api_key else "",
                          gateway_connected=bool(deployment.values.get("gateway_checked")),
                          models_checked=bool(model_service.configuration),
                          jev=deployment.values.get("jev"), storage_configured=bool(archive.museum),
                          slack_configured=bool(settings.slack_client_id),
                          slack_installed=bool(await store.setting("slack_bot_token") or settings.slack_bot_token),
                          workspace=settings.slack_team_id, identity_verified=verified,
                          allowed_email_domain=settings.allowed_email_domain,
                          timezone=deployment.values.get("timezone", "UTC"))
        return result

    @api.post("/setup/claim")
    async def claim(request: Request):
        same_origin(request)
        body = await request.json()
        value = body.get("code", "")
        async with deployment.lock:
            if deployment.complete:
                raise HTTPException(409, "This installation is already configured. Sign in with Slack.")
            attempts = deployment.values.get("claim_attempts", [])
            attempts = [stamp for stamp in attempts if stamp > time.time() - 900]
            if len(attempts) >= 10:
                raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
            if (not isinstance(value, str) or not deployment.claim_path.exists()
                    or not secrets.compare_digest(value, deployment.claim_path.read_text().strip())):
                deployment.save(claim_attempts=attempts + [time.time()])
                raise HTTPException(403, "That setup code is not valid. Find it in your deployment’s private settings.")
            grant = secrets.token_urlsafe(40)
            deployment.save(claim_digest=hashlib.sha256(grant.encode()).hexdigest(),
                            claim_expires=time.time() + 7200, claim_attempts=[])
            # Rotate the code after use. Recovery remains available to the operator
            # through the private state directory, never the public web page.
            deployment._atomic(deployment.claim_path, secrets.token_urlsafe(32))
            request.session["setup_grant"] = grant
        return await status(request)

    @api.put("/setup/gateway")
    async def connect_gateway(body: GatewayConnection, request: Request):
        require(request)
        key = body.api_key or settings.litellm_api_key
        if not key:
            raise HTTPException(422, "Enter your LiteLLM API key.")
        async with deployment.lock:
            candidate_settings = settings.model_copy(update={"litellm_base_url": body.base_url, "litellm_api_key": key})
            async with Gateway(candidate_settings, gateway.transport) as candidate:
                catalog = await candidate.catalog(refresh=True)
                candidate_service = ModelSettingsService(candidate_settings, store, candidate, archive)
                defaults = await candidate_service.defaults()
                jev = await candidate.probe_jev(defaults.classifier_model)
            deployment.save(config={"litellm_base_url": body.base_url, "litellm_api_key": key},
                            gateway_checked=True, jev=jev)
            await store.delete_setting(model_service.key)
            model_service.configuration, model_service.revision, model_service.last_checks = None, 0, {}
            gateway.root = body.base_url.removesuffix("/v1")
            gateway.http.headers["Authorization"] = "Bearer " + key
            gateway._models, gateway._models_until = None, 0
        return {"status": "connected", "models": defaults.model_dump(),
                "catalog": [m.model_dump() for m in catalog], "jev": jev}

    @api.get("/setup/models")
    async def models(request: Request):
        require(request)
        result = await model_service.describe()
        result["catalogs"] = {kind: [m.model_dump() for m in await gateway.models(kind=kind)]
                              for kind in ["chat", "embedding", "generation"]}
        return result

    @api.put("/setup/models")
    async def save_models(body: SetupModels, request: Request):
        require(request)
        result = await model_service.save(body.models, expected_revision=body.revision)
        deployment.save(jev=result["checks"]["jev"])
        return result

    @api.post("/setup/check-jev")
    async def check_jev(request: Request):
        require(request)
        result = await gateway.probe_jev()
        deployment.save(jev=result)
        return result

    @api.get("/setup/slack-manifest")
    async def manifest(request: Request):
        require(request)
        value = slack_manifest(settings.public_base_url.rstrip("/"))
        return {"create_url": "https://api.slack.com/apps?" + urlencode({"new_app": "1", "manifest_json": json.dumps(value)}),
                "manifest": value}

    @api.put("/setup/slack")
    async def connect_slack(body: SlackConnection, request: Request):
        require(request)
        async with deployment.lock:
            deployment.save(config={"slack_" + key: value for key, value in body.model_dump().items()})
            update_slack()
        return {"status": "saved"}

    @api.post("/setup/slack-install")
    async def slack_install(request: Request):
        require(request)
        if not settings.slack_client_id:
            raise HTTPException(409, "Connect your Slack app first.")
        nonce = secrets.token_urlsafe(32)
        deployment.save(install_nonce=hashlib.sha256(nonce.encode()).hexdigest())
        state = URLSafeTimedSerializer(settings.session_secret, salt="slack-install").dumps({"setup": nonce})
        return {"url": "https://slack.com/oauth/v2/authorize?" + urlencode({
            "client_id": settings.slack_client_id, "scope": ",".join(BOT_SCOPES), "state": state,
            "redirect_uri": settings.public_base_url + "/slack/install/callback"})}

    @api.post("/setup/finish")
    async def finish(body: FinishSetup, request: Request):
        require(request)
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(body.timezone)
        except ZoneInfoNotFoundError:
            raise HTTPException(422, "Choose a valid timezone.") from None
        identity = request.session.get("identity", {})
        if (identity.get("team_id") != settings.slack_team_id
                or identity.get("user_id") != settings.slack_owner_user_id
                or not identity.get("email_verified")
                or identity.get("email", "").casefold().rsplit("@", 1)[-1] != body.allowed_email_domain.casefold()):
            raise HTTPException(403, "Verify the installer’s company Slack identity before finishing setup.")
        if (not model_service.configuration or not archive.museum
                or not (await store.setting("slack_bot_token") or settings.slack_bot_token)):
            raise HTTPException(409, "Connect and check the models and encrypted storage first.")
        owner = f"{identity['team_id']}:{identity['user_id']}"
        if not await slack.verify_member(owner, fresh=True):
            raise HTTPException(403, 'The installer must be a current eligible Slack member.')
        jev = await gateway.probe_jev()
        if jev["status"] != "ready":
            raise HTTPException(409, jev["message"])
        # Prove the actual storage interface and authenticated ciphertext, without
        # involving any member's memories. Remove the probe before activation.
        probe_id, probe_key = "setup-" + str(uuid4()), new_key()
        probe = {"id": probe_id, "probe": secrets.token_urlsafe(20)}
        await archive.museum.put(probe_id, encrypt_blob(probe, probe_key))
        try:
            verified = False
            since = 0
            while True:
                page = await archive.museum.diff(since=since)
                for entry in page:
                    if entry.get("id") == probe_id and not entry.get("isDeleted"):
                        verified = decrypt_blob(entry, probe_key) == probe
                        break
                if verified or not page:
                    break
                next_since = max(item.get("updatedAt", 0) for item in page)
                if next_since <= since:
                    break
                since = next_since
            if not verified:
                raise HTTPException(503, "Encrypted storage could not verify its saved data. Try again.")
        finally:
            await archive.museum.delete(probe_id)
        await archive.enroll(owner, {k: identity.get(k, "") for k in ["name", "avatar", "email"]})
        deployment.save(config={"allowed_email_domain": body.allowed_email_domain.casefold()}, complete=True,
                        timezone=body.timezone, jev=jev, claim_digest="", claim_expires=0)
        deployment.claim_path.unlink(missing_ok=True)
        request.session.pop("setup_grant", None)
        update_slack()
        slack.settings.allowed_slack_users = ",".join(p["id"] for p in await archive.people(Reader(owner)))
        return {"status": "ready"}

    @api.put("/setup/preferences")
    async def preferences(body: FinishSetup, request: Request):
        require(request)
        deployment.save(config={"allowed_email_domain": body.allowed_email_domain.casefold()},
                        timezone=body.timezone)
        update_slack()
        return {"status": "saved"}

    @api.post("/setup/slack-create")
    async def auto_create(request: Request):
        require(request)
        body = await request.json()
        token = body.get("configuration_token")
        if not isinstance(token, str) or not 10 < len(token) < 8000:
            raise HTTPException(422, "Enter a Slack app configuration token.")
        if settings.slack_app_id:
            raise HTTPException(409, "A Slack app is already connected. Continue with its installation.")
        async with deployment.lock, httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://slack.com/api/apps.manifest.create",
                                         headers={"Authorization": "Bearer " + token},
                                         json={"manifest": slack_manifest(settings.public_base_url.rstrip("/"))})
            data = response.json()
            if not response.is_success or not data.get("ok"):
                raise HTTPException(400, "Slack could not create the app. Check the configuration token and retry.")
            credentials = data["credentials"]
            deployment.save(config={"slack_app_id": data["app_id"], "slack_client_id": credentials["client_id"],
                                    "slack_client_secret": credentials["client_secret"],
                                    "slack_signing_secret": credentials["signing_secret"]})
            update_slack()
        return {"status": "saved"}
