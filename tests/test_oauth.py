import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from test_model_settings import application, enroll


def connect(client):
    registration = client.post('/oauth/register', json={'client_name': 'Codex',
                                 'redirect_uris': ['http://127.0.0.1:9999/callback']}).json()
    client_id = registration['client_id']
    verifier = 'A' * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    response = client.get('/oauth/authorize', params={'client_id': client_id,
                          'redirect_uri': 'http://127.0.0.1:9999/callback', 'response_type': 'code',
                          'code_challenge_method': 'S256', 'code_challenge': challenge, 'state': 'state'})
    assert response.status_code == 200
    nonce = re.search(r'name="nonce" value="([^"]+)"', response.text)[1]
    response = client.post('/oauth/authorize', data={'nonce': nonce}, follow_redirects=False)
    assert response.status_code == 303
    params = parse_qs(urlsplit(response.headers['location']).query)
    assert params['state'] == ['state']
    form = {'client_id': client_id, 'grant_type': 'authorization_code', 'code': params['code'][0],
            'code_verifier': verifier, 'redirect_uri': 'http://127.0.0.1:9999/callback'}
    response = client.post('/oauth/token', data=form)
    assert response.status_code == 200, response.text
    assert response.json()['expires_in'] == 3600
    assert client.post('/oauth/token', data=form).status_code == 400
    return client_id, response.json()


def test_oauth_rotates_refresh_tokens_rejects_replay_and_keeps_other_connections(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        client_id, original = connect(client)
        _, separate = connect(client)
        request = {'client_id': client_id, 'grant_type': 'refresh_token', 'refresh_token': original['refresh_token']}
        assert client.post('/oauth/token', data={**request, 'client_id': 'wrong'}).status_code == 400
        rotated = client.post('/oauth/token', data=request)
        assert rotated.status_code == 200
        new = rotated.json()
        assert new['refresh_token'] != original['refresh_token']
        assert client.get('/api/connections', headers={'Authorization': 'Bearer '+original['access_token']}).status_code == 401
        assert client.get('/api/connections', headers={'Authorization': 'Bearer '+new['access_token']}).status_code == 200
        assert client.post('/oauth/token', data=request).status_code == 400
        assert client.get('/api/connections', headers={'Authorization': 'Bearer '+new['access_token']}).status_code == 401
        assert client.get('/api/connections', headers={'Authorization': 'Bearer '+separate['access_token']}).status_code == 200


def test_revoking_refresh_token_disables_its_access_token_and_personal_api_tokens_cannot_consent(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        _, tokens = connect(client)
        assert client.post('/oauth/revoke', data={'token': tokens['refresh_token']}).status_code == 200
        assert client.get('/api/connections', headers={'Authorization': 'Bearer '+tokens['access_token']}).status_code == 401
        connection = client.portal.call(app.state.archive.create_token, 'T:OWNER')
        assert client.post('/oauth/authorize', data={'nonce': 'invented'},
                           headers={'Authorization': 'Bearer '+connection['token']}).status_code == 403
        assert client.post('/oauth/register', json={'redirect_uris': ['http://attacker.example/callback']}).status_code == 400
