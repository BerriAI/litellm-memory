import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from nacl.exceptions import CryptoError
from test_model_settings import application, enroll

from litellm_memory.transfer import ExportStream, read_export
from litellm_memory.transfer_cli import restore


def file_bytes(records):
    stream = ExportStream('test passphrase long enough', 'T:OWNER')
    return json.dumps(stream.header) + '\n' + ''.join(stream.line(r) for r in records) + stream.finish()


def test_encrypted_export_detects_wrong_password_reorder_truncation_and_metadata_changes():
    records = [{'owner': 'T:OWNER', 'original': {'source_id': str(i), 'content': 'PRIVATE_CANARY'}} for i in range(3)]
    data = file_bytes(records)
    assert 'PRIVATE_CANARY' not in data
    assert list(read_export(io.StringIO(data), 'test passphrase long enough')) == records
    with pytest.raises(CryptoError):
        list(read_export(io.StringIO(data), 'wrong passphrase long enough'))
    lines = data.splitlines(keepends=True)
    for changed in [lines[:-1], lines[:1] + [lines[2], lines[1]] + lines[3:], lines + ['trailing']]:
        with pytest.raises((ValueError, CryptoError)):
            list(read_export(io.StringIO(''.join(changed)), 'test passphrase long enough'))
    header = json.loads(lines[0])
    header['owner'] = 'T:OTHER'
    with pytest.raises(CryptoError):
        list(read_export(io.StringIO(json.dumps(header) + '\n' + ''.join(lines[1:])), 'test passphrase long enough'))


def test_real_api_export_roundtrip_preserves_originals_and_restore_is_private_idempotent(tmp_path):
    source_app = application(tmp_path / 'source', [])
    original = {'source_id': 'legacy-stable', 'source': 'legacy', 'content': 'PRIVATE_EXACT_TEXT  \n',
                'occurred_at': '2025-02-01T12:00:00Z', 'metadata': {'original_file': 'EXACT_ORIGINAL_BYTES\n'}}
    with TestClient(source_app) as source:
        enroll(source, source_app)
        source.post('/api/memories', json=original).raise_for_status()
        response = source.post('/api/export', json={'encrypted': True, 'passphrase': 'test passphrase long enough'})
        assert response.status_code == 200 and 'PRIVATE_EXACT_TEXT' not in response.text
        records = list(read_export(io.StringIO(response.text), 'test passphrase long enough'))
        assert records[0]['original']['content'] == original['content']
        path = tmp_path / 'export.litellm-memory'
        path.write_text(response.text)
    destination_app = application(tmp_path / 'destination', [])
    with TestClient(destination_app) as destination:
        enroll(destination, destination_app)
        token = destination.portal.call(destination_app.state.archive.create_token, 'T:OWNER')
        connection = {'url': 'https://memory.example', 'owner': 'T:OWNER', 'access_token': token['token']}
        def transport(request):
            return httpx.Response(200 if (r := destination.post(request.url.path, content=request.content,
                headers={'Content-Type': 'application/json', 'Authorization': request.headers['Authorization']})).status_code == 200
                else r.status_code, content=r.content)
        counts = restore(path, 'test passphrase long enough', connection, transport=httpx.MockTransport(transport))
        assert counts == {'verified': 1, 'restored': 1, 'duplicates': 0, 'excluded': 0}
        again = restore(path, 'test passphrase long enough', connection, transport=httpx.MockTransport(transport))
        assert again['duplicates'] == 1
        result = destination.post('/api/memories/search', json={}).json()['results'][0]
        assert result['visibility'] == 'private' and result['historical']
        restored = destination.get('/api/memories/' + result['id']).json()
        assert restored['original']['metadata'] == original['metadata']
        assert restored['created_at'].startswith('2025-02-01')
        destination.delete('/api/memories/' + result['id']).raise_for_status()
        deleted = restore(path, 'test passphrase long enough', connection, transport=httpx.MockTransport(transport))
        assert deleted['excluded'] == 1 and not deleted['restored']
        assert destination.get('/api/memories/' + result['id']).status_code == 404


def test_restore_verifies_entire_file_and_owner_before_any_upload(tmp_path):
    record = {'owner': 'T:OWNER', 'original': {'source_id': 'a', 'content': 'private'}}
    path = tmp_path / 'bad.litellm-memory'
    path.write_text('\n'.join(file_bytes([record]).splitlines()[:-1]) + '\n')
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={}))
    connection = {'url': 'https://memory.example', 'owner': 'T:OWNER', 'access_token': 'unused'}
    with pytest.raises(ValueError):
        restore(path, 'test passphrase long enough', connection, transport=transport)
    assert not calls
    path.write_text(file_bytes([record]))
    with pytest.raises(ValueError, match='identity'):
        restore(path, 'test passphrase long enough', {**connection, 'owner': 'T:OTHER'}, transport=transport)
    assert not calls


def test_export_rejects_an_unsupported_format():
    exported = file_bytes([{'owner': 'T:OWNER', 'original': {'content': 'original'}}])
    assert json.loads(exported.splitlines()[0])['format'] == 'litellm-memory-transfer'
    changed = exported.replace('litellm-memory-transfer', 'unknown-transfer', 1)
    with pytest.raises(ValueError, match='Unsupported memory export format'):
        list(read_export(io.StringIO(changed), 'test passphrase long enough'))
