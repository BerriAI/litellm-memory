"""Run inside the disposable Compose container, including after restart."""

import asyncio
import json
import uuid

import httpx
import yaml

from moegent.config import Settings
from moegent.crypto import KeyVault, decrypt_blob, encrypt_blob, new_key
from moegent.ente import Museum
from moegent.setup import DeploymentConfig


async def main():
    settings = Settings(_env_file=None)
    vault = KeyVault(settings.state_dir, settings.vault_wrapping_key)
    config = DeploymentConfig(settings, vault)
    museum_config = yaml.safe_load((settings.state_dir / 'museum.yaml').read_text())
    assert museum_config['internal']['disable-registration'] is True
    assert 'hardcoded-ott' not in museum_config['internal']
    token = config.values['museum_token']
    museum = Museum('http://127.0.0.1:9080', token, namespace_key=vault.namespace_key())
    try:
        entity_id = 'ci-' + str(uuid.uuid4())
        key = new_key()
        original = {'text': 'DISPOSABLE_SMOKE_PRIVATE_CANARY', 'id': entity_id}
        encrypted = encrypt_blob(original, key)
        await museum.put(entity_id, encrypted)
        data = await museum.diff()
        stored = next(row for row in data if row['id'] == entity_id)
        assert original['text'] not in json.dumps(stored)
        assert decrypt_blob(stored, key) == original
        await museum.delete(entity_id)
    finally:
        await museum.close()
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=20) as client:
        health = await client.get('/health')
        assert health.json()['storage'] == 'ente'
        assert (await client.get('/api/setup/status')).json() == {'required': True, 'claimed': False}
        assert (await client.get('/api/setup/models')).status_code == 403
        code = config.claim_path.read_text().strip()
        assert (await client.post('/api/setup/claim', json={'code': code})).status_code == 200
        assert (await client.get('/api/setup/status')).json()['claimed'] is True
        assert (await client.get('/api/models')).status_code == 401
    print('PASS: bundled storage round trip, closed registration, durable credentials, and setup access control.')


if __name__ == '__main__':
    asyncio.run(main())
