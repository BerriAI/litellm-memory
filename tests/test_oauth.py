import base64
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from test_model_settings import application, enroll, session


def begin_connect(client, redirect_uri='http://127.0.0.1:9999/callback'):
    registration = client.post('/oauth/register', json={'client_name': 'Codex',
                                 'redirect_uris': [redirect_uri]}).json()
    client_id = registration['client_id']
    verifier = 'A' * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    response = client.get('/oauth/authorize', params={'client_id': client_id,
                          'redirect_uri': redirect_uri, 'response_type': 'code',
                          'code_challenge_method': 'S256', 'code_challenge': challenge, 'state': 'state'})
    assert response.status_code == 200
    nonce = re.search(r'name="nonce" value="([^"]+)"', response.text)[1]
    return client_id, verifier, nonce


def callback_from_page(response):
    class CallbackLink(HTMLParser):
        callback = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'a' and attrs.get('id') == 'client-callback':
                self.callback = attrs['href']

    page = CallbackLink()
    page.feed(response.text)
    assert page.callback
    return page.callback


def approve_connect(client, client_id, verifier, nonce):
    response = client.post('/oauth/authorize', data={'nonce': nonce}, follow_redirects=False)
    assert response.status_code == 200
    params = parse_qs(urlsplit(callback_from_page(response)).query)
    assert params['state'] == ['state']
    form = {'client_id': client_id, 'grant_type': 'authorization_code', 'code': params['code'][0],
            'code_verifier': verifier, 'redirect_uri': 'http://127.0.0.1:9999/callback'}
    for invalid in [{'code_verifier': 'B' * 50}, {'client_id': 'wrong'}, {'redirect_uri': 'https://other.example'}]:
        assert client.post('/oauth/token', data={**form, **invalid}).status_code == 400
    response = client.post('/oauth/token', data=form)
    assert response.status_code == 200, response.text
    assert response.json()['expires_in'] == 3600
    assert client.post('/oauth/token', data=form).status_code == 400
    return response.json()


def connect(client):
    client_id, verifier, nonce = begin_connect(client)
    return client_id, approve_connect(client, client_id, verifier, nonce)


def test_parallel_connection_tabs_and_invalid_nonce_do_not_replace_other_requests(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        first = begin_connect(client)
        second = begin_connect(client)
        assert client.post('/oauth/authorize', data={'nonce': 'wrong'}).status_code == 400
        assert approve_connect(client, *first)['access_token']
        assert approve_connect(client, *second)['access_token']
        replay = client.post('/oauth/authorize', data={'nonce': first[2]})
        assert replay.status_code == 400
        assert 'text/html' in replay.headers['content-type']
        assert 'Open Connections' in replay.text


def test_delayed_session_cookie_cannot_erase_connection_consent(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        stale_cookie = client.get('/api/me').request.headers['cookie']
        client_id, verifier, nonce = begin_connect(client)
        client.headers['Cookie'] = stale_cookie
        assert approve_connect(client, client_id, verifier, nonce)['access_token']


def test_consent_is_bound_to_member_and_wrong_member_cannot_consume_it(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        pending = begin_connect(client)
        enroll(client, app, 'OTHER')
        assert client.post('/oauth/authorize', data={'nonce': pending[2]}).status_code == 400
        session(client)
        assert approve_connect(client, *pending)['access_token']


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


def test_expired_consent_is_not_issued_and_new_request_recovers(tmp_path, monkeypatch):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        pending = begin_connect(client)
        future = time.time() + 601
        monkeypatch.setattr('litellm_memory.oauth.time.time', lambda: future)
        expired = client.post('/oauth/authorize', data={'nonce': pending[2]})
        assert expired.status_code == 400
        assert 'text/html' in expired.headers['content-type']
        assert 'start connecting again' in expired.text
        assert client.get('/api/connections').json()['connections'] == []
        assert approve_connect(client, *begin_connect(client))['access_token']


def test_consent_survives_restart_and_is_consumed_only_once_concurrently(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        pending = begin_connect(client)
    app = application(tmp_path, [])
    with TestClient(app) as client:
        session(client)
        def approve(_):
            return client.post('/oauth/authorize', data={'nonce': pending[2]}, follow_redirects=False).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(approve, range(2))) == [200, 400]


@pytest.mark.parametrize('redirect_uri', [
    'http://127.0.0.1:9999/callback', 'http://localhost:12345/callback',
    'http://[::1]:9999/callback', 'https://client.example/callback?context=one&label=%22%3E%3Cscript%3E',
])
def test_approval_hands_off_without_cross_origin_form_redirect_or_widening_csp(tmp_path, redirect_uri):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        client_id, verifier, nonce = begin_connect(client, redirect_uri)
        response = client.post('/oauth/authorize', data={'nonce': nonce}, follow_redirects=False)
        assert response.status_code == 200
        assert 'location' not in response.headers
        assert 'text/html' in response.headers['content-type']
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['content-security-policy'] == client.get('/').headers['content-security-policy']
        target = urlsplit(callback_from_page(response))
        registered = urlsplit(redirect_uri)
        assert (target.scheme, target.netloc, target.path) == (registered.scheme, registered.netloc, registered.path)
        params = parse_qs(target.query)
        for key, value in parse_qs(registered.query).items():
            assert params[key] == value
        assert params['state'] == ['state']
        token = client.post('/oauth/token', data={
            'client_id': client_id, 'grant_type': 'authorization_code', 'code': params['code'][0],
            'code_verifier': verifier, 'redirect_uri': redirect_uri})
        assert token.status_code == 200
        identity = client.get('/api/capture-identity', headers={'Authorization': 'Bearer ' + token.json()['access_token']})
        assert identity.json()['owner'] == 'T:OWNER'


def test_new_domain_member_finishes_setup_before_consent_instead_of_repeating_slack_login(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        client_id, verifier, _ = begin_connect(client)
        params = {'client_id': client_id, 'redirect_uri': 'http://127.0.0.1:9999/callback',
                  'response_type': 'code', 'code_challenge_method': 'S256', 'state': 'state',
                  'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
        session(client, 'TIN')
        response = client.get('/oauth/authorize', params=params, follow_redirects=False)
        assert response.status_code == 303
        target = urlsplit(response.headers['location'])
        assert target.path == '/'
        resumed = parse_qs(target.query)['connect'][0]
        assert parse_qs(resumed) == {k: [v] for k, v in params.items()}
        assert not client.get('/api/me').json()['enrolled']
        assert client.post('/api/signup').status_code == 200
        consent = client.get('/oauth/authorize?' + resumed)
        assert consent.status_code == 200
        nonce = re.search(r'name="nonce" value="([^"]+)"', consent.text)[1]
        tokens = approve_connect(client, client_id, verifier, nonce)
        identity = client.get('/api/capture-identity', headers={'Authorization': 'Bearer '+tokens['access_token']})
        assert identity.json()['owner'] == 'T:TIN'


@pytest.mark.parametrize('changes', [
    {'team_id': 'OTHER'}, {'email_verified': False}, {'email': 'tin@other.example'},
])
def test_domain_signup_still_rejects_ineligible_slack_identity(tmp_path, changes):
    import json

    from itsdangerous import TimestampSigner
    app = application(tmp_path, [])
    identity = {'team_id': 'T', 'user_id': 'TIN', 'email': 'tin@berri.ai',
                'email_verified': True, 'name': 'Tin', **changes}
    with TestClient(app) as client:
        cookie = TimestampSigner('test-session').sign(base64.b64encode(json.dumps({'identity': identity}).encode())).decode()
        client.cookies.set('litellm_memory_session', cookie)
        assert client.post('/api/signup').status_code == 403
        assert client.portal.call(app.state.archive.member, 'T:TIN') is None


def test_connector_consent_discloses_reviewed_hooks_without_executing_markup(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        hooks = [{'host': 'codex', 'command': 'python hook <script>alert(1)</script>'},
                 {'host': 'claude', 'command': 'python hook --host claude'}]
        registration = client.post('/oauth/register', json={'client_name': 'Connector',
                                   'redirect_uris': ['http://127.0.0.1:9999/callback'], 'reminder_hooks': hooks})
        assert registration.status_code == 201
        assert registration.json()['reminder_hooks'] == hooks
        response = client.get('/oauth/authorize', params={'client_id': registration.json()['client_id'],
            'redirect_uri': 'http://127.0.0.1:9999/callback', 'response_type': 'code',
            'code_challenge_method': 'S256', 'code_challenge': 'A' * 43, 'state': 'state'})
        assert response.status_code == 200
        assert 'Review reminder commands' in response.text and 'approve reminders' in response.text
        assert '&lt;script&gt;' in response.text and '<script>alert' not in response.text
        assert client.post('/oauth/register', json={'redirect_uris': ['http://127.0.0.1:9999/callback'],
            'reminder_hooks': [{'host': 'unknown', 'command': 'run'}]}).status_code == 422
