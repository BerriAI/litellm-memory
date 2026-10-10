"""Production application boundaries, using isolated storage and synthetic identities."""

import json
import time
from base64 import b64encode
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from slack_sdk.signature import SignatureVerifier
from test_model_settings import Museum

from litellm_memory.application import create_app as production_app
from litellm_memory.config import Settings
from litellm_memory.gateway import Gateway


def config(tmp_path, **extra):
    values = dict(_env_file=None, state_dir=tmp_path, session_secret="test-only",
                  litellm_base_url="https://gateway.example/v1", allowed_email_domain="example.com",
                  slack_team_id="T1", slack_owner_user_id="UOWNER", slack_bot_user_id="UBOT",
                  slack_app_id="A1", slack_signing_secret="signing-test",
                  allowed_slack_users="T1:UOWNER,T1:UMEMBER,T1:U1,T1:U2")
    return Settings(**(values | extra))


def create_app(settings, **kwargs):
    """Run the real application with enrolled test members and no external services."""
    gateway = Gateway(settings, httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [{"id": settings.litellm_model}]})))
    app = production_app(settings, museum=Museum(), gateway=gateway, **kwargs)
    members = settings.allowed_slack_users.split(',')
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original(app):
            for owner in members:
                if owner:
                    await app.state.archive.enroll(owner, {"name": owner.split(':')[1]})
            app.state.slack.settings.allowed_slack_users = ','.join(members)
            yield

    app.router.lifespan_context = lifespan
    app.state.slack.api = AsyncMock(return_value={"ok": True, "channel": {}, "messages": [], "ts": "99"})
    return app


def signed_event(client, event, event_id="Ev1", timestamp=None):
    body = json.dumps({"type": "event_callback", "team_id": "T1", "api_app_id": "A1",
                       "event_id": event_id, "event": event})
    stamp = str(timestamp or int(time.time()))
    signature = SignatureVerifier("signing-test").generate_signature(timestamp=stamp, body=body)
    return client.post("/slack/events", content=body,
                       headers={"x-slack-request-timestamp": stamp, "x-slack-signature": signature,
                                "content-type": "application/json"})


def sign_in(client, user_id="UOWNER", team_id="T1", email=None):
    identity = {"user_id": user_id, "team_id": team_id, "name": user_id,
                "email": email or user_id.lower() + "@example.com", "email_verified": True}
    cookie = TimestampSigner("test-only").sign(b64encode(json.dumps({"identity": identity}).encode())).decode()
    client.cookies.set("litellm_memory_session", cookie)


def test_signature_verification_and_challenge(tmp_path):
    with TestClient(create_app(config(tmp_path), start_workers=False)) as client:
        assert client.post("/slack/events", json={"type": "url_verification", "challenge": "x"}).status_code == 401
        assert signed_event(client, {}, timestamp=int(time.time()) - 600).status_code == 401
        body = json.dumps({"type": "url_verification", "challenge": "verified"})
        stamp = str(int(time.time()))
        sig = SignatureVerifier("signing-test").generate_signature(timestamp=stamp, body=body)
        assert client.post("/slack/events", content=body, headers={
            "x-slack-request-timestamp": stamp, "x-slack-signature": sig
        }).json() == {"challenge": "verified"}


def test_owner_invitation_and_out_of_order_events_survive_restart(tmp_path):
    settings = config(tmp_path)
    app = create_app(settings, start_workers=False)
    with TestClient(app) as client:
        event = {"type": "member_joined_channel", "user": "UBOT", "channel": "C1",
                 "inviter": "UOTHER", "event_ts": "1"}
        assert signed_event(client, event).status_code == 200
        assert not client.portal.call(app.state.store.channel_allowed, "T1", "C1")
        event.update(inviter="UOWNER", event_ts="3")
        signed_event(client, event, "Ev2")
        assert client.portal.call(app.state.store.channel_allowed, "T1", "C1")
        event.update(inviter="UOTHER", event_ts="2")
        signed_event(client, event, "Ev3")
        assert client.portal.call(app.state.store.channel_allowed, "T1", "C1")
        event.update(user="USOMEONE", event_ts="9")
        signed_event(client, event, "Ev4")
        assert client.portal.call(app.state.store.channel_allowed, "T1", "C1")
    restarted = create_app(settings, start_workers=False)
    with TestClient(restarted) as client:
        assert client.portal.call(restarted.state.store.channel_allowed, "T1", "C1")
        signed_event(client, {"type": "member_left_channel", "user": "UBOT", "channel": "C1", "event_ts": "5"}, "Ev5")
        assert not client.portal.call(restarted.state.store.channel_allowed, "T1", "C1")


@pytest.mark.parametrize("team,user,email,expected", [
    ("T1", "UOWNER", "owner@example.com", 202),
    ("T1", "UMEMBER", "member@example.com", 202),
    ("T1", "UOUTSIDER", "outsider@example.com", 401),
    ("T2", "UOWNER", "owner@example.com", 403),
    ("T1", "UOWNER", "owner@another.example", 403),
])
def test_browser_queries_require_enrollment_workspace_and_domain(tmp_path, team, user, email, expected):
    app = create_app(config(tmp_path), start_workers=False)
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"question": "hello"}).status_code == 401
        sign_in(client, user, team, email)
        assert client.post("/api/chat", json={"question": "hello"}).status_code == expected


def test_web_jobs_are_member_scoped_and_sharing_requires_same_origin(tmp_path):
    with TestClient(create_app(config(tmp_path), start_workers=False)) as client:
        sign_in(client)
        job = client.post("/api/chat", json={"question": "hello"}).json()
        assert client.get("/api/jobs/" + job["id"]).status_code == 200
        sign_in(client, "UMEMBER")
        assert client.get("/api/jobs/" + job["id"]).status_code == 404
        assert client.put("/api/preferences", json={"capture_enabled": False},
                          headers={"origin": "https://evil.example"}).status_code == 403
        client.cookies.clear()
        assert client.get("/api/jobs/" + job["id"]).status_code == 401


@pytest.mark.asyncio
async def test_storage_recovers_running_job_without_losing_answer(tmp_path):
    from litellm_memory.storage import Storage
    store = Storage(tmp_path / "db")
    await store.open()
    job_id = await store.enqueue("owner", "web", {"question": "hello"})
    await store.next_job()
    await store.result(job_id, answer="saved", status="running")
    await store.close()
    await store.open()
    job = await store.next_job()
    assert job["answer"] == "saved"
    assert job["attempts"] == 2
    await store.close()
