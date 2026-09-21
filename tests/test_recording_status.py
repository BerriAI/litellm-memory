import time

from fastapi.testclient import TestClient
from test_model_settings import application, enroll, session


def test_recording_receipt_is_private_and_history_is_not_live_recording(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.get('/api/v1/recording-status').status_code == 401
        enroll(client, app)
        initial = client.get('/api/v1/recording-status').json()
        assert initial['received'] == initial['connected_clients'] == 0
        assert initial['last_received_at'] is None
        assert initial['capture_enabled'] is True

        history = {'source_id': 'history', 'source': 'codex', 'content': 'PRIVATE_HISTORY', 'historical': True}
        assert client.post('/api/v1/memories', json=history).status_code == 200
        imported = client.get('/api/v1/recording-status').json()
        assert imported['stats']['total'] == 1 and imported['received'] == 0

        token = client.portal.call(app.state.archive.create_token, 'T:OWNER', 'Coding tool')
        assert client.get('/api/v1/recording-status').json()['connected_clients'] == 1
        record = client.post('/api/v1/memories', json={
            'source_id': 'first', 'source': 'codex', 'content': 'PRIVATE_FIRST_CAPTURE',
        }).json()['id']
        response = client.get('/api/v1/recording-status')
        saved = response.json()
        assert saved['received'] == 1 and saved['last_received_at'] > 0
        assert 'PRIVATE_' not in response.text and record not in response.text
        assert response.headers['cache-control'] == 'no-store'

        enroll(client, app, 'OTHER')
        other = client.get('/api/v1/recording-status?owner=T:OWNER').json()
        assert other['received'] == other['connected_clients'] == other['stats']['total'] == 0
        session(client)
        assert client.put('/api/v1/preferences', json={'capture_enabled': False}).status_code == 200
        paused = client.get('/api/v1/recording-status').json()
        assert paused['capture_enabled'] is False and paused['received'] == 1
        assert client.delete('/api/v1/connections/' + token['id']).status_code == 200
        assert client.get('/api/v1/recording-status').json()['connected_clients'] == 0
        assert client.delete('/api/v1/memories/' + record).status_code == 200
        assert client.get('/api/v1/recording-status').json()['received'] == 0


def test_unstored_intake_and_expired_connections_do_not_look_ready(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        expired = client.portal.call(app.state.archive.create_token, 'T:OWNER', 'Expired', -1)
        assert client.get('/api/v1/recording-status').json()['connected_clients'] == 0
        assert client.get('/api/v1/connections').json()['connections'][0]['connected'] == 0

        async def add_refresh():
            await app.state.archive.db.execute('INSERT INTO refresh_tokens VALUES(?,?,?,?,0)',
                                              ('test-digest', expired['id'], 'test-client', time.time() + 3600))
            await app.state.archive.db.commit()
        client.portal.call(add_refresh)
        assert client.get('/api/v1/recording-status').json()['connected_clients'] == 1

        record = client.post('/api/v1/memories', json={'source_id': 'receipt', 'content': 'Work'}).json()['id']

        async def awaiting_storage():
            await app.state.archive.db.execute("UPDATE records SET status='storing' WHERE id=?", (record,))
            await app.state.archive.db.commit()
        client.portal.call(awaiting_storage)
        status = client.get('/api/v1/recording-status').json()
        assert status['received'] == 0 and status['last_received_at'] is None
        assert status['stats']['awaiting_storage'] == 1
