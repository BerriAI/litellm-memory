from fastapi.testclient import TestClient
from test_model_settings import application, enroll


def test_chunked_body_limit_is_enforced_without_content_length(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        def chunks():
            yield b'{"content":"'
            for _ in range(12):
                yield b'x' * 100000
            yield b'"}'
        response = client.post('/api/memories', content=chunks(), headers={'Content-Type': 'application/json'})
        assert response.status_code == 413
        assert response.headers['cache-control'] == 'no-store'


def test_dynamic_oauth_client_registration_is_rate_limited(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        for _ in range(12):
            assert client.post('/oauth/register', json={'redirect_uris': ['http://localhost:9988/cb']}).status_code == 201
        response = client.post('/oauth/register', json={'redirect_uris': ['http://localhost:9988/cb']})
        assert response.status_code == 429 and 'retry-after' in response.headers
