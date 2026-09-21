from fastapi.testclient import TestClient
from test_model_settings import application, enroll


def test_versioned_capture_and_legacy_clients_share_identity_and_access(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.get('/api/v1/me').json()['signed_in'] is False
        assert client.get('/api/v1/models').status_code == 401
        enroll(client, app)
        body = {'source_id': 'version-upgrade', 'content': 'PRIVATE_VERSION_CANARY'}
        saved = client.post('/api/v1/memories', json=body)
        assert saved.status_code == 200
        identity = saved.json()['id']
        assert client.post('/api/memories', json=body).json()['id'] == identity
        for prefix in ['/api/v1', '/api']:
            result = client.get(f'{prefix}/memories/{identity}')
            assert result.status_code == 200 and result.json()['content'] == body['content']
            assert result.headers['cache-control'] == 'no-store'
            assert client.get(f'{prefix}/me').json()['enrolled']
            assert client.put(f'{prefix}/view-preferences', json={'hidden_types': ['decision']},
                              headers={'Origin': 'https://elsewhere.example'}).status_code == 403
        token = client.portal.call(app.state.archive.create_token, 'T:OWNER', 'API version test')
        for prefix in ['/api/v1', '/api']:
            assert client.get(f'{prefix}/memories/{identity}',
                              headers={'Authorization': 'Bearer ' + token['token']}).status_code == 200
            assert client.put(f'{prefix}/view-preferences', json={},
                              headers={'Authorization': 'Bearer ' + token['token']}).status_code == 403
        enroll(client, app, 'OTHER')
        for prefix in ['/api/v1', '/api']:
            result = client.get(f'{prefix}/memories/{identity}')
            assert result.status_code == 404 and body['content'] not in result.text


def test_openapi_documents_v1_and_preserves_response_contracts(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        paths = client.get('/openapi.json').json()['paths']
        assert '/api/v1/setup/status' in paths
        assert '/api/v1/transfer-inbox/{identity}' in paths
        assert '/api/v1/chat' in paths and '202' in paths['/api/v1/chat']['post']['responses']
        assert all(path.startswith('/api/v1/') for path in paths if path.startswith('/api/'))
        operation_ids = [operation['operationId'] for path in paths.values() for operation in path.values()]
        assert len(operation_ids) == len(set(operation_ids))
        assert client.get('/api/v2/me').status_code == 404
        assert client.get('/api/v1/setup/status').json() == client.get('/api/setup/status').json()
        enroll(client, app)
        invalid = client.post('/api/v1/chat', json={'question': {'secret': 'SCHEMA_CANARY'}})
        assert invalid.status_code == 422 and 'SCHEMA_CANARY' not in invalid.text
        job = client.post('/api/v1/chat', json={'question': 'My recent work?'})
        assert job.status_code == 202
        identity = job.json()['id']
        assert client.get(f'/api/jobs/{identity}').status_code == 200
        assert client.post(f'/api/v1/jobs/{identity}/cancel').status_code == 200


def test_version_aliases_share_rate_limits_and_body_limits(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        for index in range(6):
            prefix = '/api/v1' if index % 2 else '/api'
            assert client.post(prefix + '/export').status_code == 401
        for prefix in ['/api/v1', '/api']:
            response = client.post(prefix + '/export')
            assert response.status_code == 429 and 'retry-after' in response.headers
        response = client.post('/api/v1/memories', content=b'x' * 1_000_001)
        assert response.status_code == 413 and response.headers['cache-control'] == 'no-store'


def test_recovery_mode_denies_both_api_versions(tmp_path):
    app = application(tmp_path, [])
    app.state.settings.recovery_mode = True
    with TestClient(app) as client:
        for path in ['/api/setup/status', '/api/v1/setup/status', '/api/v1/models', '/api/v1/me']:
            assert client.get(path).status_code == 503
        assert client.get('/health').status_code == 200
