import json
import time

from fastapi.testclient import TestClient
from nacl.public import PublicKey, SealedBox
from test_model_settings import application, enroll, session

from litellm_memory.crypto import b64, encode, unb64

SOURCE = 'https://source.example'
DESTINATION = 'https://destination.example'


def apps(tmp_path):
    a, b = application(tmp_path / 'a', []), application(tmp_path / 'b', [])
    a.state.settings.public_base_url, b.state.settings.public_base_url = SOURCE, DESTINATION
    return a, b


def prepare(source, destination):
    invitation = destination.post('/api/transfers/receive', json={'source': SOURCE})
    invitation.raise_for_status()
    outgoing = source.post('/api/transfers/send', json=invitation.json())
    outgoing.raise_for_status()
    frames = [source.get(f"/api/transfers/{outgoing.json()['id']}/packets/{i}").json()
              for i in range(outgoing.json()['frames'])]
    return invitation.json(), outgoing.json(), frames


def test_browser_transfer_preserves_private_originals_and_resumes_after_restart(tmp_path):
    a, b = apps(tmp_path)
    original = {'source_id': 'original', 'source': 'legacy', 'content': 'PRIVATE_MIGRATION_CANARY  \n',
                'occurred_at': '2024-02-01T12:00:00Z', 'metadata': {'original_file': 'ORIGINAL_BYTES\n'}}
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a, 'OTHER')
        source.post('/api/memories', json={'source_id': 'other', 'content': 'OTHER_PERSON_SECRET'}).raise_for_status()
        enroll(source, a)
        enroll(destination, b)
        saved = source.post('/api/memories', json=original).json()
        source.post('/api/memories', json={'source_id': 'undated', 'content': 'Undated original'}).raise_for_status()
        invitation, outgoing, frames = prepare(source, destination)
        assert outgoing['records'] == 2 and 'private_key' not in invitation
        identity = invitation['id']
        for frame in frames:
            assert 'PRIVATE_MIGRATION_CANARY' not in frame['ciphertext']
            destination.post(f'/api/transfers/{identity}/packets', json=frame).raise_for_status()
            assert destination.post(f'/api/transfers/{identity}/packets', json=frame).json()['duplicate']
        assert destination.post('/api/memories/search', json={}).json()['results'] == []
        assert destination.post(f'/api/transfers/{identity}/finish').status_code == 202
        assert destination.post(f'/api/transfers/{identity}/finish').status_code == 202
        destination.portal.call(b.state.transfers.step, 1)
        assert destination.get(f'/api/transfers/{identity}').json()['restored'] == 1
        for path in (tmp_path / 'b').glob('transfers.sqlite3*'):
            assert b'PRIVATE_MIGRATION_CANARY' not in path.read_bytes()
        source.delete(f"/api/transfers/{outgoing['id']}").raise_for_status()
        assert source.get('/api/memories/' + saved['id']).status_code == 200  # Copy, not deletion.
    # A new process has the durable encrypted queue/checkpoint and can continue.
    restarted = application(tmp_path / 'b', [])
    restarted.state.settings.public_base_url = DESTINATION
    with TestClient(restarted, base_url=DESTINATION) as destination:
        session(destination)
        destination.portal.call(restarted.state.transfers.step, 32)
        result = destination.get(f'/api/transfers/{identity}').json()
        assert result['state'] == 'complete' and result['restored'] == 2
        records = destination.post('/api/memories/search', json={}).json()['results']
        assert len(records) == 2 and all(r['historical'] and r['visibility'] == 'private' for r in records)
        restored = destination.get('/api/memories/' + saved['id']).json()
        assert restored['original']['content'] == original['content']
        assert restored['original']['metadata'] == original['metadata']
        assert restored['created_at'].startswith('2024-02-01')
        internal = destination.portal.call(restarted.state.transfers.get, identity, 'T:OWNER')
        assert 'private_key' not in internal['payload']
        async def chunks():
            return (await (await restarted.state.transfers.db.execute('SELECT count(*) FROM transfer_chunks')).fetchone())[0]
        assert destination.portal.call(chunks) == 0


def test_transfer_rejects_wrong_account_origin_packets_and_incomplete_manifest(tmp_path):
    a, b = apps(tmp_path)
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a)
        enroll(destination, b)
        source.post('/api/memories', json={'source_id': 'one', 'content': 'Private text'}).raise_for_status()
        invitation, outgoing, frames = prepare(source, destination)
        identity = invitation['id']
        token = source.portal.call(a.state.archive.create_token, 'T:OWNER')
        assert source.post('/api/transfers/send', json=invitation, headers={'Authorization': 'Bearer ' + token['token']}).status_code == 403
        assert source.post('/api/transfers/send', json={**invitation, 'owner': 'T:OTHER'}).status_code == 400
        assert destination.post(f'/api/transfers/{identity}/packets', json=frames[0], headers={'Origin': SOURCE}).status_code == 403
        enroll(destination, b, 'OTHER')
        assert destination.get(f'/api/transfers/{identity}').status_code == 404
        assert destination.post(f'/api/transfers/{identity}/packets', json=frames[0]).status_code == 404
        session(destination)
        assert destination.post(f'/api/transfers/{identity}/packets', json=frames[1]).status_code == 400
        corrupted = dict(frames[0])
        raw = bytearray(unb64(corrupted['ciphertext']))
        raw[-1] ^= 1
        corrupted['ciphertext'] = b64(raw)
        assert destination.post(f'/api/transfers/{identity}/packets', json=corrupted).status_code == 400
        destination.post(f'/api/transfers/{identity}/packets', json=frames[0]).raise_for_status()
        assert destination.post(f'/api/transfers/{identity}/finish').status_code == 400
        # Even a correctly encrypted final packet must bind the complete ordered
        # ciphertext sequence and exact record count before any source is saved.
        manifest = {'version': 1, 'grant': identity, 'source': SOURCE, 'destination': DESTINATION,
                    'owner': 'T:OWNER', 'index': 1, 'kind': 'manifest', 'records': 999, 'digest': '0' * 64}
        forged = {'index': 1, 'ciphertext': b64(SealedBox(PublicKey(unb64(invitation['public_key']))).encrypt(encode(manifest)))}
        destination.post(f'/api/transfers/{identity}/packets', json=forged).raise_for_status()
        assert destination.post(f'/api/transfers/{identity}/finish').status_code == 400
        assert destination.post('/api/memories/search', json={}).json()['results'] == []
        assert destination.post(f'/api/transfers/{identity}/packets', json=frames[1]).status_code == 400  # No packet replacement.
        destination.delete(f'/api/transfers/{identity}').raise_for_status()
        assert destination.post(f'/api/transfers/{identity}/finish').status_code == 400
        assert 'private_key' not in destination.portal.call(b.state.transfers.get, identity, 'T:OWNER')['payload']


def test_transfer_honors_deletion_pause_and_revocation(tmp_path):
    a, b = apps(tmp_path)
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a)
        enroll(destination, b)
        original = {'source_id': 'one', 'content': 'Original'}
        source.post('/api/memories', json=original).raise_for_status()
        deleted = destination.post('/api/memories', json=original).json()
        destination.delete('/api/memories/' + deleted['id']).raise_for_status()
        invitation, outgoing, frames = prepare(source, destination)
        identity = invitation['id']
        for frame in frames:
            destination.post(f'/api/transfers/{identity}/packets', json=frame).raise_for_status()
        destination.post(f'/api/transfers/{identity}/finish').raise_for_status()
        async def pause():
            await b.state.archive.preferences('T:OWNER', capture_enabled=False)
        destination.portal.call(pause)
        assert not destination.portal.call(b.state.transfers.step)
        assert destination.get(f'/api/transfers/{identity}').json()['state'] == 'waiting'
        async def resume():
            await b.state.archive.preferences('T:OWNER', capture_enabled=True)
            await b.state.transfers.db.execute('UPDATE transfers SET next_attempt=0')
            await b.state.transfers.db.commit()
        destination.portal.call(resume)
        destination.portal.call(b.state.transfers.step)
        status = destination.get(f'/api/transfers/{identity}').json()
        assert status['state'] == 'complete' and status['excluded'] == 1 and status['restored'] == 0
        assert destination.get('/api/memories/' + deleted['id']).status_code == 404
        source.delete('/api/memories/' + deleted['id']).raise_for_status()
        assert source.get(f"/api/transfers/{outgoing['id']}/packets/0").status_code == 403


def test_expired_grants_and_staging_are_removed(tmp_path):
    a, b = apps(tmp_path)
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a)
        enroll(destination, b)
        invitation, outgoing, frames = prepare(source, destination)
        identity = invitation['id']
        destination.post(f'/api/transfers/{identity}/packets', json=frames[0]).raise_for_status()
        async def expire():
            await b.state.transfers.db.execute('UPDATE transfers SET expires=?', (time.time() - 1,))
            await b.state.transfers.db.commit()
            await b.state.transfers.cleanup()
            return (await (await b.state.transfers.db.execute('SELECT count(*) FROM transfer_chunks')).fetchone())[0]
        assert destination.portal.call(expire) == 0
        assert destination.get(f'/api/transfers/{identity}').status_code == 404
        assert destination.post(f'/api/transfers/{identity}/packets', json=frames[0]).status_code == 404
        assert 'PRIVATE' not in json.dumps(destination.get('/api/transfers').json())


def test_leaving_cancels_import_and_erases_receiving_key(tmp_path):
    a, b = apps(tmp_path)
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a)
        enroll(destination, b)
        for identity in ['a', 'b']:
            source.post('/api/memories', json={'source_id': identity, 'content': identity}).raise_for_status()
        invitation, _, frames = prepare(source, destination)
        identity = invitation['id']
        for frame in frames:
            destination.post(f'/api/transfers/{identity}/packets', json=frame).raise_for_status()
        destination.post(f'/api/transfers/{identity}/finish').raise_for_status()
        destination.portal.call(b.state.transfers.step, 1)
        destination.post('/api/leave').raise_for_status()
        destination.portal.call(b.state.transfers.step)
        internal = destination.portal.call(b.state.transfers.get, identity, 'T:OWNER')
        assert internal['state'] == 'cancelled' and internal['payload'] == {}
        assert internal['restored'] == 1
        assert destination.get(f'/api/transfers/{identity}').status_code == 401


def test_transfer_staging_is_bounded_and_accepts_only_its_recipient(tmp_path, monkeypatch):
    a, b = apps(tmp_path)
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a)
        enroll(destination, b)
        source.post('/api/memories', json={'source_id': 'a', 'content': 'one record'}).raise_for_status()
        invitation, _, frames = prepare(source, destination)
        second = destination.post('/api/transfers/receive', json={'source': SOURCE}).json()
        assert destination.post('/api/transfers/receive', json={'source': SOURCE}).status_code == 400
        assert destination.post(f"/api/transfers/{second['id']}/packets", json=frames[0]).status_code == 400
        monkeypatch.setattr('litellm_memory.migration.MAX_TOTAL_STAGED_BYTES', 10)
        assert destination.post(f"/api/transfers/{invitation['id']}/packets", json=frames[0]).status_code == 400
        assert destination.post('/api/memories/search', json={}).json()['results'] == []


def test_receiving_capability_is_origin_bound_upload_only_and_resumable(tmp_path):
    a, b = apps(tmp_path)
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a)
        enroll(destination, b)
        source.post('/api/memories', json={'source_id': 'a', 'content': 'ONLY_THE_OWNER_CAN_READ_THIS'}).raise_for_status()
        invitation, outgoing, frames = prepare(source, destination)
        context = source.get(f"/api/transfers/{outgoing['id']}/outgoing").json()
        assert context['invitation'] == invitation and context['frames'] == len(frames)
        path = f"/api/transfer-inbox/{invitation['id']}"
        headers = {'Origin': SOURCE, 'Authorization': 'Bearer ' + invitation['upload_token']}
        destination.cookies.clear()  # Cross-origin requests never use the destination's session.
        preflight = destination.options(path, headers={'Origin': SOURCE, 'Access-Control-Request-Method': 'POST',
                                                       'Access-Control-Request-Headers': 'authorization,content-type'})
        assert preflight.status_code == 200 and preflight.headers['Access-Control-Allow-Origin'] == SOURCE
        assert 'Access-Control-Allow-Credentials' not in preflight.headers
        for forbidden in [dict(headers, Origin='https://untrusted.example'),
                          dict(headers, Authorization='Bearer wrong'), {'Origin': SOURCE},
                          {'Authorization': headers['Authorization']}]:
            result = destination.get(path, headers=forbidden)
            assert result.status_code == 403 and 'Access-Control-Allow-Origin' not in result.headers
        assert destination.get('/api/me', headers=headers).json()['signed_in'] is False
        assert destination.post('/api/memories/search', json={}, headers=headers).status_code == 403
        assert destination.get('/api/transfers/' + invitation['id'], headers=headers).status_code == 403
        assert 'ONLY_THE_OWNER' not in destination.get(path, headers=headers).text
        for frame in frames:
            response = destination.post(path, headers=headers, json={'action': 'packet', 'packet': frame})
            assert response.status_code == 200 and response.headers['Access-Control-Allow-Origin'] == SOURCE
        destination.post(path, headers=headers, json={'action': 'finish'}).raise_for_status()
        destination.portal.call(b.state.transfers.step)
        assert destination.get(path, headers=headers).json()['state'] == 'complete'
        assert destination.post(path, headers=headers, json={'action': 'packet', 'packet': frames[0]}).status_code == 400
        session(destination)
        results = destination.post('/api/memories/search', json={}).json()['results']
        assert len(results) == 1 and results[0]['visibility'] == 'private'


def test_sender_csp_only_adds_one_canonical_validated_origin(tmp_path):
    from litellm_memory.migration import origin

    assert origin('https://DESTINATION.example:443/') == DESTINATION
    assert origin('http://[::1]:8882') == 'http://[::1]:8882'
    app = application(tmp_path, [])
    with TestClient(app) as client:
        valid = client.get('/transfer/send', params={'destination': DESTINATION})
        assert f"connect-src 'self' {DESTINATION};" in valid.headers['Content-Security-Policy']
        main = client.get('/', params={'destination': DESTINATION})
        assert DESTINATION not in main.headers['Content-Security-Policy']
        for malicious in ["https://evil.example;script-src *", 'https://evil.example\r\nX-Injected:yes',
                          'https://evil.example\t', 'https://evil.example\\@good.example',
                          'https://user:password@evil.example', 'https://evil.example/path',
                          'https://evil.example:0', 'https://evil.example:bad', 'http://evil.example',
                          'https://evil.example#fragment', 'https://evil.example?query',
                          "https://evil.example'", 'https://evil.example%20']:
            response = client.get('/transfer/send', params={'destination': malicious})
            assert "connect-src 'self';" in response.headers['Content-Security-Policy']
            assert 'evil.example' not in response.headers['Content-Security-Policy']
            assert 'X-Injected' not in response.headers


def test_cancel_during_import_never_resurrects_the_grant(tmp_path, monkeypatch):
    import asyncio

    a, b = apps(tmp_path)
    with TestClient(a, base_url=SOURCE) as source, TestClient(b, base_url=DESTINATION) as destination:
        enroll(source, a)
        enroll(destination, b)
        for identity in ['a', 'b']:
            source.post('/api/memories', json={'source_id': identity, 'content': identity}).raise_for_status()
        invitation, _, frames = prepare(source, destination)
        identity = invitation['id']
        for frame in frames:
            destination.post(f'/api/transfers/{identity}/packets', json=frame).raise_for_status()
        destination.post(f'/api/transfers/{identity}/finish').raise_for_status()
        original = b.state.archive.capture

        async def cancel_in_flight():
            entered, release = asyncio.Event(), asyncio.Event()

            async def capture(*args, **kwargs):
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)

            monkeypatch.setattr(b.state.archive, 'capture', capture)
            importing = asyncio.create_task(b.state.transfers.step())
            await entered.wait()
            cancellation = asyncio.create_task(b.state.transfers.cancel(identity, 'T:OWNER'))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(importing, cancellation)
            return await b.state.transfers.get(identity, 'T:OWNER')

        result = destination.portal.call(cancel_in_flight)
        assert result['state'] == 'cancelled' and result['restored'] == 1 and result['payload'] == {}
        assert not destination.portal.call(b.state.transfers.step)
