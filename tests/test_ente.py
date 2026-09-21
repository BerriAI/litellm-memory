import httpx
import pytest

from litellm_memory.crypto import KeyVault, decrypt_blob, encrypt_blob, new_key
from litellm_memory.ente import Museum


@pytest.mark.asyncio
async def test_entity_namespace_is_created_before_first_record_and_retry_uses_stable_id(tmp_path):
    namespace = None
    entities = {}
    methods = []
    def handler(request):
        import json
        nonlocal namespace
        methods.append((request.method, request.url.path))
        assert request.headers['X-Auth-Token'] == 'private-token'
        assert request.headers['X-Client-Package'] == 'io.ente.photos'
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith('/key'):
            if request.method == 'POST':
                namespace = body
                return httpx.Response(200)
            return httpx.Response(200, json=namespace) if namespace else httpx.Response(404)
        assert namespace is not None
        if request.url.path.endswith('/diff'):
            return httpx.Response(200, json={'diff': list(entities.values())})
        if request.method == 'DELETE':
            entities.pop(request.url.params['id'], None)
            return httpx.Response(200)
        if request.method == 'POST' and body['id'] in entities:
            return httpx.Response(500)
        entities[body['id']] = body
        return httpx.Response(200, json=body)
    key = new_key()
    value = {'content': 'Private namespace roundtrip'}
    museum = Museum('https://museum.example', 'private-token', httpx.MockTransport(handler),
                    namespace_key=KeyVault(tmp_path).namespace_key())
    try:
        blob = encrypt_blob(value, key)
        await museum.put('one', blob)
        await museum.put('one', blob)
        assert len(entities) == 1
        assert methods[:3] == [('GET', '/user-entity/key'), ('POST', '/user-entity/key'), ('GET', '/user-entity/key')]
        assert decrypt_blob((await museum.diff())[0], key) == value
        assert ('PUT', '/user-entity/entity') in methods
        await museum.delete('one')
        assert not entities
    finally:
        await museum.close()
