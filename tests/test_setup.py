import json
from base64 import b64decode, b64encode

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from test_archive import FakeMuseum
from test_model_settings import application

from litellm_memory.slack_manifest import BOT_SCOPES


def claim(client, app):
    code = app.state.deployment.claim_path.read_text()
    response = client.post('/api/setup/claim', json={'code': code})
    assert response.status_code == 200, response.text
    return code


def verified_owner(client):
    signer = TimestampSigner('test-session')
    cookie = client.cookies.get('litellm_memory_session')
    session = json.loads(b64decode(signer.unsign(cookie)))
    session['identity'] = {'team_id': 'T', 'user_id': 'OWNER', 'email': 'owner@berri.ai',
                           'email_verified': True, 'name': 'Owner'}
    client.cookies.clear()
    client.cookies.set('litellm_memory_session', signer.sign(b64encode(json.dumps(session).encode())).decode())


def test_setup_claim_is_required_rotates_code_and_never_returns_credentials(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.get('/api/setup/status').json() == {'required': True, 'claimed': False}
        assert client.get('/api/setup/models').status_code == 403
        assert client.put('/api/setup/gateway', json={'base_url': 'https://gateway.example', 'api_key': 'SECRET'}).status_code == 403
        assert client.post('/api/setup/claim', json={'code': 'wrong'}).status_code == 403
        code = claim(client, app)
        assert app.state.deployment.claim_path.read_text() != code
        assert client.post('/api/setup/claim', json={'code': code}).status_code == 403
        response = client.put('/api/setup/gateway', json={'base_url': 'https://gateway.example', 'api_key': 'SENSITIVE_GATEWAY_SECRET'})
        assert response.status_code == 200, response.text
        assert response.json()['models']['agent_model'] == 'chat-a'
        assert 'SENSITIVE_GATEWAY_SECRET' not in response.text
        assert 'SENSITIVE_GATEWAY_SECRET' not in app.state.deployment.path.read_text()
        assert 'SENSITIVE_GATEWAY_SECRET' not in client.get('/api/setup/status').text
        app.state.deployment.values['claim_expires'] = 0
        assert client.get('/api/setup/models').status_code == 403
    restarted = application(tmp_path, [])
    assert restarted.state.settings.litellm_api_key == 'SENSITIVE_GATEWAY_SECRET'
    assert restarted.state.gateway.root == 'https://gateway.example'


def test_missing_jev_keeps_gateway_progress_and_blocks_activation(tmp_path):
    app = application(tmp_path, [], jev_status=403)
    with TestClient(app) as client:
        claim(client, app)
        connected = client.put('/api/setup/gateway', json={'base_url': 'https://gateway.example', 'api_key': 'secret'})
        assert connected.status_code == 200
        assert connected.json()['jev']['status'] == 'access_denied'
        assert client.get('/api/setup/status').json()['gateway_connected'] is True
        checked = client.put('/api/setup/models', json={'models': {}, 'revision': 0})
        assert checked.status_code == 400 and 'TypeSafe Jev' in checked.text
        assert client.post('/api/setup/finish', json={}).status_code == 403
        assert not app.state.deployment.complete


def test_setup_finishes_only_with_verified_installer_models_and_real_storage_round_trip(tmp_path):
    class Museum(FakeMuseum):
        async def diff(self, **kwargs):
            return [{'id': key, **value, 'updatedAt': 1} for key, value in self.entities.items()]
    app = application(tmp_path, [])
    museum = Museum()
    app.state.archive.museum = museum
    with TestClient(app) as client:
        code = claim(client, app)
        assert client.post('/api/setup/finish', json={}).status_code == 403
        client.put('/api/setup/gateway', json={'base_url': 'https://gateway.example', 'api_key': 'secret'})
        assert client.put('/api/setup/models', json={'models': {}, 'revision': 0}).status_code == 200
        verified_owner(client)
        assert client.post('/api/setup/finish', json={}).status_code == 409
        client.portal.call(app.state.store.setting, 'slack_bot_token', 'TEST-BOT-TOKEN')
        assert client.post('/api/setup/finish', json={}).status_code == 409
        client.portal.call(app.state.store.setting, 'slack_scopes', ','.join(BOT_SCOPES))
        async def slack_api(method, payload):
            assert method == 'users.info'
            return {'user': {'id': 'OWNER', 'team_id': 'T', 'profile': {'email': 'owner@berri.ai'}}}
        app.state.slack.api = slack_api
        response = client.post('/api/setup/finish', json={'timezone': 'America/Los_Angeles'})
        assert response.status_code == 200, response.text
        assert app.state.deployment.complete
        assert museum.writes == 1 and not museum.entities
        assert not app.state.deployment.claim_path.exists()
        assert client.post('/api/setup/claim', json={'code': code}).status_code == 409
        assert client.get('/api/setup/models').status_code == 403
        assert client.get('/api/me').json()['enrolled']
        assert client.get('/api/setup/status').json() == {'required': False, 'claimed': False}


def test_setup_detects_installed_slack_app_missing_required_scopes(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        claim(client, app)
        assert client.get('/api/setup/status').json()['slack_missing_scopes'] == []
        client.portal.call(app.state.store.setting, 'slack_bot_token', 'PRIVATE_BOT_TOKEN')
        previous_scopes = set(BOT_SCOPES) - {'users:read', 'users:read.email'}
        client.portal.call(app.state.store.setting, 'slack_scopes', ','.join(previous_scopes))
        result = client.get('/api/setup/status')
        assert result.json()['slack_installed'] is True
        assert result.json()['slack_missing_scopes'] == ['users:read', 'users:read.email']
        assert 'PRIVATE_BOT_TOKEN' not in result.text
        client.portal.call(app.state.store.setting, 'slack_scopes', ','.join(BOT_SCOPES))
        assert client.get('/api/setup/status').json()['slack_missing_scopes'] == []


def test_setup_rejects_cross_origin_and_credentials_in_gateway_url(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.post('/api/setup/claim', json={'code': app.state.deployment.claim_path.read_text()},
                           headers={'Origin': 'https://attacker.example'}).status_code == 403
        claim(client, app)
        result = client.put('/api/setup/gateway', json={'base_url': 'https://user:PRIVATE_SECRET@example.com/v1'})
        assert result.status_code == 422 and 'PRIVATE_SECRET' not in result.text
        result = client.put('/api/setup/slack', json={'app_id': 'invalid', 'client_secret': 'PRIVATE_SECRET'})
        assert result.status_code == 422 and 'PRIVATE_SECRET' not in result.text
