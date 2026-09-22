import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from test_model_settings import application, enroll, session

from litellm_memory import connector
from litellm_memory.device_oauth import digest


def begin(client):
    response = client.post('/oauth/device/code', json={'client_name': 'Codex and Claude Code',
        'reminder_hooks': [{'host': 'codex', 'command': 'review-this-command'}]})
    assert response.status_code == 200
    device = response.json()
    assert device['device_code'] not in device['verification_uri_complete']
    assert 'no-store' in response.headers['cache-control']
    return device


def consent(client, device):
    response = client.get(device['verification_uri_complete'])
    assert response.status_code == 200
    assert 'review-this-command' in response.text
    assert device['device_code'] not in response.text
    return {'nonce': re.search(r'name="nonce" value="([^"]+)"', response.text)[1],
            'user_code': device['user_code'], 'decision': 'approve'}


def approve(client, device):
    response = client.post('/oauth/device/approve', data=consent(client, device), follow_redirects=False)
    assert response.status_code == 303
    assert urlsplit(response.headers['location']).path == '/oauth/device'
    page = client.get(response.headers['location'])
    assert 'Finishing setup' in page.text and '127.0.0.1' not in page.text


def redeem(client, device, **changes):
    return client.post('/oauth/token', data={'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
        'device_code': device['device_code'], 'client_id': device['client_id'], **changes})


def status(client, device):
    return client.get('/oauth/device/status', params={'user_code': device['user_code']})


def complete(client, device, result='complete'):
    return client.post('/oauth/device/complete', data={'device_code': device['device_code'],
                       'client_id': device['client_id'], 'status': result})


def test_hosted_approval_requires_real_completion_and_refresh_works(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        device = begin(client)
        approve(client, device)
        assert status(client, device).json()['status'] == 'approved'
        assert complete(client, device).status_code == 400
        token = redeem(client, device).json()
        assert token['access_token']
        assert status(client, device).json()['status'] == 'installing'
        assert redeem(client, device).json()['error'] == 'invalid_grant'
        assert complete(client, device).status_code == 200
        assert complete(client, device).status_code == 200  # Lost completion response is safe to retry.
        state = status(client, device).json()
        assert state == {'status': 'complete', 'next': '/?setup=codex#connections'}
        assert not any(key in json.dumps(state) for key in ['access_token', 'device_code', token['access_token']])
        rotated = client.post('/oauth/token', data={'grant_type': 'refresh_token',
            'refresh_token': token['refresh_token'], 'client_id': device['client_id']})
        assert rotated.status_code == 200


def test_device_secrets_owner_nonce_and_client_cannot_be_substituted(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        first, second = begin(client), begin(client)
        form = consent(client, first)
        enroll(client, app, 'OTHER')
        assert client.post('/oauth/device/approve', data=form).status_code == 400
        session(client)
        assert client.post('/oauth/device/approve', data={**form, 'user_code': second['user_code']}).status_code == 400
        approve(client, first)
        assert redeem(client, first, device_code=first['user_code']).status_code == 400
        assert redeem(client, first, client_id=second['client_id']).status_code == 400
        token = redeem(client, first).json()
        assert token['access_token']
        session(client, 'OTHER')
        assert status(client, first).status_code == 403
        assert client.get(first['verification_uri_complete']).status_code == 403
        assert status(client, second).status_code == 403
        assert complete(client, {**first, 'device_code': second['device_code']}).status_code == 400
        assert client.post('/oauth/device/approve', data=form,
                           headers={'Authorization': 'Bearer ' + token['access_token']}).status_code == 403


def test_device_polling_pending_slow_down_expiry_and_denial(tmp_path, monkeypatch):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        device = begin(client)
        assert redeem(client, device).json()['error'] == 'authorization_pending'
        assert redeem(client, device).json()['error'] == 'slow_down'
        future = time.time() + 10
        monkeypatch.setattr('litellm_memory.device_oauth.time.time', lambda: future)
        assert redeem(client, device).json()['error'] == 'authorization_pending'
        form = consent(client, device)
        client.post('/oauth/device/approve', data={**form, 'decision': 'deny'})
        assert redeem(client, device).json()['error'] == 'access_denied'
        assert status(client, device).json()['status'] == 'denied'
        assert client.get('/api/connections').json()['connections'] == []
        future += 601
        assert redeem(client, device).json()['error'] == 'expired_token'
        assert status(client, device).json()['status'] == 'expired'
        assert 'Start a new connection' in client.get(device['verification_uri_complete']).text


def test_device_setup_failure_revokes_only_attempted_connection(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        unrelated = client.portal.call(app.state.archive.create_token, 'T:OWNER')
        device = begin(client)
        approve(client, device)
        token = redeem(client, device).json()
        assert complete(client, device, 'failed').status_code == 200
        assert complete(client, device).status_code == 400
        assert status(client, device).json()['status'] == 'failed'
        assert client.get('/api/capture-identity', headers={'Authorization': 'Bearer ' + token['access_token']}).status_code == 401
        assert client.get('/api/capture-identity', headers={'Authorization': 'Bearer ' + unrelated['token']}).status_code == 200


def test_device_new_member_signin_resume_and_reload_survive_restart(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        device = begin(client)
        redirect = client.get(device['verification_uri_complete'], follow_redirects=False)
        assert redirect.headers['location'].startswith('/auth/slack?')
        session(client, 'TIN')
        redirect = client.get(device['verification_uri_complete'], follow_redirects=False)
        assert parse_qs(urlsplit(redirect.headers['location']).query)['device'] == [device['user_code']]
        assert client.post('/api/signup').status_code == 200
        form = consent(client, device)
    app = application(tmp_path, [])
    with TestClient(app) as client:
        session(client, 'TIN')
        assert client.post('/oauth/device/approve', data=form, follow_redirects=False).status_code == 303
        assert client.post('/oauth/device/approve', data=form).status_code == 400
        assert 'Finishing setup' in client.get(device['verification_uri_complete']).text
        token = redeem(client, device).json()
        identity = client.get('/api/capture-identity', headers={'Authorization': 'Bearer ' + token['access_token']})
        assert identity.json()['owner'] == 'T:TIN'


def test_device_code_consumed_once_concurrently_and_state_encrypted(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        device = begin(client)
        approve(client, device)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: redeem(client, device), range(2)))
        assert sorted(r.status_code for r in responses) == [200, 400]
        async def row():
            return await (await app.state.store.db.execute('SELECT * FROM device_grants WHERE digest=?',
                                                           (digest(device['device_code']),))).fetchone()
        raw = client.portal.call(row)
        assert raw['payload'].startswith('enc:v1:')
        assert device['device_code'] not in str(tuple(raw))
        assert device['user_code'] not in str(tuple(raw))


@pytest.mark.parametrize('failure', [None, 'token', 'identity', 'binding', 'setup', 'completion'])
def test_connector_hosted_login_commits_before_acknowledgement(tmp_path, monkeypatch, failure):
    real_client = httpx.Client
    local = connector.LocalCapture(tmp_path)
    old = {'url': 'https://memory.example', 'owner': 'T:OTHER' if failure == 'binding' else 'T:OWNER'}
    local.set('account_binding', old)
    local.db.commit()
    calls, sleeps, opened, polls = [], [], [], []
    reminders = [{'host': 'codex', 'command': 'reviewed'}, {'host': 'claude', 'command': 'reviewed-too'}]

    def remote(request):
        calls.append(request.url.path)
        assert request.url.host == 'memory.example'
        if request.url.path == '/oauth/device/code':
            assert json.loads(request.content)['reminder_hooks'] == reminders
            return httpx.Response(200, json={'client_id': 'client', 'device_code': 'private-code',
                'verification_uri_complete': 'https://memory.example/oauth/device?user_code=browser-code',
                'interval': 3, 'expires_in': 600, 'reminder_hooks': reminders})
        if request.url.path == '/oauth/token':
            form = parse_qs(request.content.decode())
            assert form['device_code'] == ['private-code']
            polls.append(True)
            if len(polls) < 3:
                return httpx.Response(400, json={'error': 'authorization_pending' if len(polls) == 1 else 'slow_down'})
            return httpx.Response(400 if failure == 'token' else 200, json={
                'access_token': 'secret-access', 'refresh_token': 'secret-refresh', 'expires_in': 3600})
        if request.url.path == '/api/capture-identity':
            return httpx.Response(403 if failure == 'identity' else 200, json={'owner': 'T:OWNER'})
        assert request.url.path == '/oauth/device/complete'
        desired = parse_qs(request.content.decode())['status'][0]
        check = connector.LocalCapture(tmp_path)
        try:
            if desired == 'complete':
                assert check.get('hosts') == ['codex', 'claude']
                assert check.get('status') == 'connected'
            else:
                assert failure and check.get('connection') is None
        finally:
            check.close()
        return httpx.Response(503 if failure == 'completion' else 200, json={'status': desired})

    def setup():
        assert local.get('connection')['owner'] == 'T:OWNER'
        if failure == 'setup':
            raise ValueError('Private failure details')
        local.set('hosts', ['codex', 'claude'])

    monkeypatch.setattr(connector.httpx, 'Client', lambda **kw: real_client(**kw, transport=httpx.MockTransport(remote)))
    monkeypatch.setattr(connector.time, 'sleep', sleeps.append)
    try:
        if failure not in {None, 'completion'}:
            with pytest.raises((httpx.HTTPError, ValueError)):
                connector.login(local, 'https://memory.example', opener=opened.append, on_connected=setup, reminder_hooks=reminders)
            assert local.get('connection') is None
            assert local.get('account_binding') == old
        else:
            connector.login(local, 'https://memory.example', opener=opened.append, on_connected=setup, reminder_hooks=reminders)
            assert local.get('status') == 'connected'
        assert sleeps[:3] == [3, 3, 8]
        assert opened == ['https://memory.example/oauth/device?user_code=browser-code']
        assert '/oauth/register' not in calls
    finally:
        local.close()


def test_device_consent_cannot_be_replayed_through_pkce_and_external_origin_is_rejected(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        device = begin(client)
        form = consent(client, device)
        assert client.post('/oauth/device/approve', data=form, headers={'Origin': 'https://other.example'}).status_code == 403
        assert client.post('/oauth/authorize', data={'nonce': form['nonce']}).status_code == 400
        assert client.get('/oauth/authorize', params={'client_id': device['client_id']}).status_code == 400
        approve(client, device)
        assert redeem(client, device).status_code == 200


@pytest.mark.parametrize('failure', ['approval_url', 'reminders', 'cancel', 'expired'])
def test_connector_stops_for_invalid_setup_or_terminal_decision(tmp_path, monkeypatch, failure):
    real_client = httpx.Client
    opened = []
    def remote(request):
        if request.url.path == '/oauth/device/code':
            return httpx.Response(200, json={'client_id': 'client', 'device_code': 'private-code',
                'verification_uri_complete': 'https://' + ('other.example' if failure == 'approval_url' else 'memory.example') + '/oauth/device',
                'interval': 3, 'expires_in': 600, 'reminder_hooks': [{'host':'claude','command':'unexpected'}] if failure == 'reminders' else []})
        if request.url.path == '/oauth/token':
            return httpx.Response(400, json={'error': 'access_denied' if failure == 'cancel' else 'expired_token'})
        return httpx.Response(400, json={})
    monkeypatch.setattr(connector.httpx, 'Client', lambda **kw: real_client(**kw, transport=httpx.MockTransport(remote)))
    monkeypatch.setattr(connector.time, 'sleep', lambda _: None)
    local = connector.LocalCapture(tmp_path)
    try:
        with pytest.raises(connector.ConnectionSetupError):
            connector.login(local, 'https://memory.example', opener=opened.append)
        assert local.get('connection') is None
        if failure in {'approval_url', 'reminders'}:
            assert opened == []
    finally:
        local.close()
