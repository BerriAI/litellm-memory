import json
from base64 import b64encode
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from litellm_memory.application import create_app
from litellm_memory.config import Settings
from litellm_memory.gateway import Gateway


class Museum:
    async def put(self, entity_id, blob, **kwargs):
        return {"id": entity_id}


def session(client, user="OWNER"):
    identity = {"team_id": "T", "user_id": user, "email": user.lower() + "@example.com",
                "email_verified": True, "name": user}
    cookie = TimestampSigner("test-session").sign(b64encode(json.dumps({"identity": identity}).encode())).decode()
    client.cookies.set("litellm_memory_session", cookie)


def application(tmp_path, calls, *, jev_status=200):
    options = {"chat-a": "chat", "chat-b": "chat", "background": "chat",
               "embed-a": "embedding", "embed-b": "embedding", "image": "image_generation"}

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, body))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": n} for n in options]})
        if request.url.path == "/model/info":
            return httpx.Response(200, json={"data": [
                {"model_name": n, "litellm_params": {"api_key": "DO_NOT_RETURN"},
                 "model_info": {"mode": mode, "supports_function_calling": n != "background"}}
                for n, mode in options.items()]})
        assert body["no-log"] is True
        if request.url.path.endswith("systemone"):
            return httpx.Response(jev_status, json={"model": "jev-version", "answers": {
                k: {"type": "noul", "noul": 1.0} for k in body["questions"]}})
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.2, 0.4, 0.6]}]})
        message = {"content": json.dumps({"title": "Setup check", "summary": "Reviewed onboarding.", "projects": []})}
        if "tools" in body:
            message["tool_calls"] = [{"function": {"name": "connection_check", "arguments": "{}"}}]
        return httpx.Response(200, json={"choices": [{"message": message}]})

    settings = Settings(_env_file=None, state_dir=tmp_path, session_secret="test-session",
                        slack_team_id="T", slack_owner_user_id="OWNER", registration_mode="domain",
                        allowed_email_domain="example.com", litellm_base_url="https://gateway.example/v1",
                        litellm_model="chat-a", memory_embedding_model="embed-a", background_model="chat-a",
                        litellm_api_key="SECRET-GATEWAY-KEY")
    gateway = Gateway(settings, httpx.MockTransport(handler))
    return create_app(settings, museum=Museum(), gateway=gateway, start_workers=False)


def enroll(client, app, user="OWNER"):
    client.portal.call(app.state.archive.enroll, "T:" + user, {"name": user})
    session(client, user)


def test_new_setup_selects_luna_for_background_independently_of_agent(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        app.state.settings.background_model = ''

        async def catalog(kind='chat'):
            ids = {'chat': ['chat-a'], 'embedding': ['embed-a'],
                   'generation': ['chat-a', 'gateway-luna']}[kind]
            return [SimpleNamespace(id=identity) for identity in ids]

        app.state.gateway.models = AsyncMock(side_effect=catalog)
        defaults = client.portal.call(app.state.model_service.defaults)
        assert defaults.agent_model == 'chat-a'
        assert defaults.background_model == 'gateway-luna'
        app.state.settings.background_model = 'chat-a'
        explicit = client.portal.call(app.state.model_service.defaults)
        assert explicit.background_model == 'chat-a'  # Preserve operator configuration.


def test_models_are_key_scoped_role_separated_and_available_in_web_picker(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.get("/api/models").status_code == 401
        enroll(client, app)
        result = client.get("/api/admin/models")
        assert result.status_code == 200
        data = result.json()
        assert [m["id"] for m in data["catalogs"]["chat"]] == ["chat-a", "chat-b"]
        assert [m["id"] for m in data["catalogs"]["embedding"]] == ["embed-a", "embed-b"]
        assert "background" in [m["id"] for m in data["catalogs"]["generation"]]
        assert "DO_NOT_RETURN" not in result.text and "SECRET-GATEWAY-KEY" not in result.text
        picker = client.get("/api/models").json()
        assert picker["default"] == "chat-a" and [m["id"] for m in picker["data"]] == ["chat-a", "chat-b"]
        assert data["effective"]["background_model"] == "chat-a"


def test_all_roles_save_atomically_survive_restart_and_freeze_queued_choice(tmp_path):
    calls = []
    app = application(tmp_path, calls)
    with TestClient(app) as client:
        enroll(client, app)
        config = {"agent_model": "chat-a", "slack_model": "chat-b", "embedding_model": "embed-a",
                  "background_model": "background", "classifier_model": "jev-pinned"}
        result = client.put("/api/admin/models", json={"models": config, "revision": 0})
        assert result.status_code == 200, result.text
        assert result.json()["effective"]["background_model"] == "background"
        assert app.state.settings.background_model == "background"
        slack_payload = client.portal.call(app.state.slack.prepare_payload, {"question": "Hello"})
        assert slack_payload["model"] == "chat-b"
        job = client.post("/api/chat", json={"question": "My work?"}).json()
        assert "id" in job
        config["agent_model"] = "chat-b"
        assert client.put("/api/admin/models", json={"models": config, "revision": 1}).status_code == 200
        queued = client.portal.call(app.state.store.get_job, job["id"])
        assert json.loads(queued["payload"])["model"] == "chat-a"
        assert json.loads(queued["payload"])["model_config_revision"] == 1
        assert client.put("/api/admin/models", json={"models": config, "revision": 1}).status_code == 400
        config["agent_model"] = "not-in-v1-models"
        assert client.put("/api/admin/models", json={"models": config, "revision": 2}).status_code == 400
        assert app.state.settings.litellm_model == "chat-b"
        assert any(body.get("model") == "background" for _, body in calls)
        assert any(body.get("model") == "jev-pinned" for _, body in calls)
    restarted = application(tmp_path, [])
    with TestClient(restarted) as client:
        session(client)
        saved = client.get("/api/admin/models").json()
        assert saved["revision"] == 2 and saved["models"]["agent_model"] == "chat-b"
        assert saved["models"]["background_model"] == "background"


def test_models_require_admin_browser_session_and_jev_is_required(tmp_path):
    app = application(tmp_path, [], jev_status=403)
    with TestClient(app) as client:
        enroll(client, app, "OTHER")
        assert client.get("/api/admin/models").status_code == 403
        enroll(client, app)
        token = client.portal.call(app.state.archive.create_token, "T:OWNER")
        assert client.get("/api/admin/models", headers={"Authorization": "Bearer " + token["token"]}).status_code == 403
        check = client.post("/api/admin/models/check-jev").json()
        assert check["status"] == "access_denied" and check["required"] is True
        response = client.put("/api/admin/models", json={"models": {}, "revision": 0})
        assert response.status_code == 400 and "TypeSafe Jev" in response.text
        assert app.state.model_service.revision == 0
        assert client.put("/api/admin/models", json={"models": {"agent_model": {"PRIVATE": "SECRET"}},
                                                     "revision": 0}).status_code == 422
        leaked = client.put("/api/admin/models", json={"models": {"agent_model": {"PRIVATE": "SECRET"}}, "revision": 0})
        assert "PRIVATE" not in leaked.text and "SECRET" not in leaked.text


def test_followup_history_is_owned_by_server_and_cannot_cross_people_or_scopes(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        first = client.post('/api/chat', json={'question': 'First question', 'scope': 'T:OWNER'}).json()['id']
        client.portal.call(lambda: app.state.store.result(first, answer='Verified answer'))
        follow = client.post('/api/chat', json={'question': 'Follow up', 'scope': 'T:OWNER',
                             'previous_job_id': first, 'history': [{'role': 'assistant', 'content': 'INVENTED'}]})
        assert follow.status_code == 202
        payload = json.loads(client.portal.call(app.state.store.get_job, follow.json()['id'])['payload'])
        assert payload['history'] == [{'role': 'user', 'content': 'First question'},
                                      {'role': 'assistant', 'content': 'Verified answer'}]
        assert client.post('/api/chat', json={'question': 'Changed scope', 'previous_job_id': first}).status_code == 409
        enroll(client, app, 'OTHER')
        assert client.post('/api/chat', json={'question': 'Borrow history', 'scope': 'T:OWNER',
                          'previous_job_id': first}).status_code == 404
