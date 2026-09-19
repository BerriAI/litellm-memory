import json
import time

import pytest
from fastapi.testclient import TestClient
from slack_sdk.signature import SignatureVerifier

from moegent.app import create_app
from moegent.config import Settings


def config(tmp_path, **extra):
    return Settings(_env_file=None, state_dir=tmp_path, session_secret="test-only", memory_warm_on_start=False,
                    slack_team_id="T1", slack_owner_user_id="UOWNER", slack_bot_user_id="UBOT",
                    slack_app_id="A1", slack_signing_secret="signing-test", **extra)


def signed_event(client, event, event_id="Ev1", timestamp=None):
    body = json.dumps({"type": "event_callback", "team_id": "T1", "api_app_id": "A1",
                       "event_id": event_id, "event": event})
    stamp = str(timestamp or int(time.time()))
    signature = SignatureVerifier("signing-test").generate_signature(timestamp=stamp, body=body)
    return client.post("/slack/events", content=body,
                       headers={"x-slack-request-timestamp": stamp, "x-slack-signature": signature,
                                "content-type": "application/json"})


def test_signature_verification_and_challenge(tmp_path):
    with TestClient(create_app(config(tmp_path), start_worker=False)) as client:
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
    app = create_app(settings, start_worker=False)
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
        # A different member's invitation cannot grant or revoke the bot's membership.
        event.update(user="USOMEONE", event_ts="9")
        signed_event(client, event, "Ev4")
        assert client.portal.call(app.state.store.channel_allowed, "T1", "C1")
    restarted = create_app(settings, start_worker=False)
    with TestClient(restarted) as client:
        assert client.portal.call(restarted.state.store.channel_allowed, "T1", "C1")
        signed_event(client, {"type": "member_left_channel", "user": "UBOT", "channel": "C1", "event_ts": "5"}, "Ev5")
        assert not client.portal.call(restarted.state.store.channel_allowed, "T1", "C1")


def test_slack_dedupe_and_public_vs_allowlist(tmp_path):
    event = {"type": "app_mention", "user": "UANYONE", "channel": "C1", "text": "<@UBOT> hello", "ts": "10"}
    app = create_app(config(tmp_path), start_worker=False)
    with TestClient(app) as client:
        signed_event(client, event)
        signed_event(client, event)
        job = client.portal.call(app.state.store.next_job)
        assert job["payload"]["question"] == "hello"
        assert client.portal.call(app.state.store.next_job) is None
        assert not client.portal.call(app.state.slack.permitted, job["payload"])
        client.portal.call(app.state.store.channel_event, "T1", "C1", True, 11)
        assert client.portal.call(app.state.slack.permitted, job["payload"])
    gated = create_app(config(tmp_path / "gated", access_mode="allowlist", allowed_slack_users="T1:UOWNER"), start_worker=False)
    with TestClient(gated) as client:
        signed_event(client, event)
        assert client.portal.call(gated.state.store.next_job) is None
        assert client.post("/api/chat", json={"question": "hello"}).status_code == 401
        assert client.get("/api/memories/team").status_code == 401
        assert client.post("/api/memories/search", json={"query": "team"}).status_code == 401
        # Query parameters cannot impersonate a signed-in Slack user.
        assert client.post("/api/chat?user_id=UOWNER&team_id=T1", json={"question": "hello"}).status_code == 401


def test_web_queue_session_isolation_validation_and_origin(tmp_path):
    app = create_app(config(tmp_path), start_worker=False)
    with TestClient(app) as client:
        job = client.post("/api/chat", json={"question": "hello"}).json()
        assert client.get("/api/jobs/" + job["id"]).status_code == 200
        client.cookies.clear()
        assert client.get("/api/jobs/" + job["id"]).status_code == 404
        assert client.post("/api/chat", json={"question": "hello"}, headers={"origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/chat", json={"question": "hello", "history": [{"role": "system", "content": "x"}]}).status_code == 422
        assert client.post("/api/chat", json={"question": " "}).status_code == 422


def test_chat_completes_with_worker(tmp_path):
    async def fake_answer(settings, question, history):
        return "A sourced answer to " + question
    with TestClient(create_app(config(tmp_path), agent_fn=fake_answer)) as client:
        job = client.post("/api/chat", json={"question": "hello"}).json()
        for _ in range(30):
            result = client.get("/api/jobs/" + job["id"]).json()
            if result["status"] == "done":
                break
            time.sleep(0.1)
        assert result["answer"] == "A sourced answer to hello"


def test_missing_inviter_fails_closed_and_dm_is_allowed(tmp_path):
    app = create_app(config(tmp_path), start_worker=False)
    with TestClient(app) as client:
        signed_event(client, {"type": "member_joined_channel", "user": "UBOT", "channel": "C1", "event_ts": "1"})
        assert not client.portal.call(app.state.store.channel_allowed, "T1", "C1")
        assert client.portal.call(app.state.slack.permitted, {"user": "UANYONE", "team": "T1", "channel": "D123"})


@pytest.mark.asyncio
async def test_storage_recovers_running_job_without_losing_answer(tmp_path):
    from moegent.storage import Storage
    path = tmp_path / "db"
    store = Storage(path)
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
