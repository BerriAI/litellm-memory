from fastapi.testclient import TestClient
from test_model_settings import application, enroll, session


def test_key_rotation_checks_candidate_before_commit_and_keeps_secret_out_of_responses(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        connection = client.get('/api/admin/connection')
        assert connection.status_code == 200 and 'SECRET' not in connection.text
        result = client.put('/api/admin/connection/key', json={'api_key': 'NEW_SECRET_FOR_ROTATION'})
        assert result.status_code == 200, result.text
        assert 'NEW_SECRET' not in result.text and 'NEW_SECRET' not in app.state.deployment.path.read_text()
        assert app.state.settings.litellm_api_key == 'NEW_SECRET_FOR_ROTATION'
        enroll(client, app, 'OTHER')
        assert client.put('/api/admin/connection/key', json={'api_key': 'forbidden'}).status_code == 403
    denied = application(tmp_path, [], jev_status=403)
    with TestClient(denied) as client:
        session(client)
        assert client.put('/api/admin/connection/key', json={'api_key': 'WRONG_NEW_KEY'}).status_code == 400
        assert denied.state.settings.litellm_api_key == 'NEW_SECRET_FOR_ROTATION'


def test_admin_transfer_changes_admin_only_without_granting_private_memory_access(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app, 'OTHER')
        enroll(client, app)
        saved = client.post('/api/memories', json={'source_id': 'private', 'content': 'OWNER_PRIVATE_CANARY'}).json()
        assert client.post('/api/admin/transfer', json={'owner': 'T:UNKNOWN'}).status_code == 404
        assert client.post('/api/admin/transfer', json={'owner': 'T:OTHER'}).status_code == 200
        assert client.get('/api/admin/models').status_code == 403
        assert client.get('/api/memories/' + saved['id']).status_code == 200
        session(client, 'OTHER')
        assert client.get('/api/admin/models').status_code == 200
        assert client.get('/api/memories/' + saved['id']).status_code == 404
    restarted = application(tmp_path, [])
    assert restarted.state.settings.slack_owner_user_id == 'OTHER'


def test_recovery_mode_exposes_only_liveness_and_blocks_web_mcp_and_slack(tmp_path):
    app = application(tmp_path, [])
    app.state.settings.recovery_mode = True
    with TestClient(app) as client:
        assert client.get('/health').json()['status'] == 'recovery'
        for path in ['/', '/mcp', '/slack/events', '/api/chat', '/api/memories']:
            response = client.post(path, json={})
            assert response.status_code == 503 and response.headers['Cache-Control'] == 'no-store'
