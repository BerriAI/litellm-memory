"""Run inside the disposable Compose container, including after restart."""

import asyncio
import json
import uuid
from datetime import datetime

import httpx
import yaml
from nacl.public import PublicKey, SealedBox

from moegent.archive import Archive, Reader, WorkInput
from moegent.config import Settings
from moegent.crypto import KeyVault, b64, decrypt_blob, encode, encrypt_blob, new_key, unb64
from moegent.ente import Museum
from moegent.migration import Packet, Transfers
from moegent.setup import DeploymentConfig
from moegent.time_settings import DisplayPreferences, ScheduleSettings


async def main():
    settings = Settings(_env_file=None)
    vault = KeyVault(settings.state_dir, settings.vault_wrapping_key)
    config = DeploymentConfig(settings, vault)
    # Finish live setup mutations before this fixture seeds files directly. The
    # supported deployment has one configuration writer; a second stale writer
    # must not be introduced by the persistence test itself.
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=20) as client:
        health = await client.get('/health')
        assert health.json()['storage'] == 'ente'
        assert (await client.get('/api/setup/status')).json() == {'required': True, 'claimed': False}
        assert (await client.get('/api/setup/models')).status_code == 403
        code = config.claim_path.read_text().strip()
        assert (await client.post('/api/setup/claim', json={'code': code})).status_code == 200
        assert (await client.get('/api/setup/status')).json()['claimed'] is True
        assert (await client.get('/api/models')).status_code == 401
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
        # Exercise the application keys/catalog as well as Ente across restart
        # and full volume+Postgres restore. This synthetic owner never signs in
        # to Slack or runs models; processing is paused before capture.
        archive = Archive(settings.state_dir / 'memory.sqlite3', vault, museum)
        await archive.open()
        try:
            await archive.enroll('CI:OWNER', {'name': 'Disposable CI fixture'})
            await archive.preferences('CI:OWNER', agent_enabled=False)
            calendar_marker = settings.state_dir / 'ci-calendar-proof'
            if not calendar_marker.exists():
                config.save(timezone='America/Los_Angeles', daily_time='07:15', schedule_revision=1)
                await archive.save_display_preferences('CI:OWNER', DisplayPreferences(timezone='Asia/Tokyo'))
                calendar_marker.write_text('calendar-v1')
            assert config.values['timezone'] == 'America/Los_Angeles' and config.values['daily_time'] == '07:15'
            assert (await archive.display_preferences('CI:OWNER')).timezone == 'Asia/Tokyo'
            # Zone data must be available inside the shipped image, including DST.
            schedule = ScheduleSettings(timezone=config.values['timezone'])
            assert schedule.next_run(datetime.fromisoformat('2026-03-08T09:59:00Z')) == '2026-03-08T10:00:00+00:00'
            saved = await archive.capture('CI:OWNER', WorkInput(source_id='persistent-proof',
                content='DISPOSABLE_PERSISTENCE_CANARY', occurred_at='2026-01-01T00:00:00Z'))
            original = (await archive.read(Reader('CI:OWNER'), saved['id']))['original']
            assert original['content'] == 'DISPOSABLE_PERSISTENCE_CANARY'
            remote = next(row for row in await museum.diff() if row['id'] == saved['id'] and not row.get('isDeleted'))
            assert decrypt_blob(remote, await archive.key('CI:OWNER'))['original'] == original
            deleted = WorkInput(source_id='deletion-proof', content='DISPOSABLE_DELETED_CANARY')
            try:
                receipt = await archive.capture('CI:OWNER', deleted)
            except ValueError:
                pass  # The preserved tombstone correctly rejects replay after restart/restore.
            else:
                await archive.delete('CI:OWNER', receipt['id'])
            try:
                await archive.capture('CI:OWNER', deleted)
            except ValueError:
                pass
            else:
                raise AssertionError('Deleted source was resurrected')
            # A paused browser transfer's encrypted queue and recipient key must
            # survive the same container restart and clean-volume restore.
            transfers = Transfers(settings, archive)
            await transfers.open()
            try:
                marker = settings.state_dir / 'ci-transfer-grant'
                if not marker.exists():
                    invitation = await transfers.receive('CI:OWNER', 'https://ci-source.example')
                    marker.write_text(invitation['id'])
                    value = {'version': 1, 'grant': invitation['id'], 'source': invitation['source'],
                             'destination': invitation['destination'], 'owner': 'CI:OWNER', 'index': 0,
                             'kind': 'records', 'records': [{'owner': 'CI:OWNER', 'original': {
                                 'source_id': 'ci-transfer', 'content': 'DISPOSABLE_TRANSFER_CANARY'}}]}
                    ciphertext = b64(SealedBox(PublicKey(unb64(invitation['public_key']))).encrypt(encode(value)))
                    await transfers.accept(invitation['id'], 'CI:OWNER', Packet(index=0, ciphertext=ciphertext))
                row = await transfers.get(marker.read_text(), 'CI:OWNER')
                staged = await (await transfers.db.execute('SELECT ciphertext FROM transfer_chunks WHERE transfer_id=?',
                                                          (row['id'],))).fetchone()
                assert 'DISPOSABLE_TRANSFER_CANARY' not in staged[0]
                assert transfers.decode(row, staged[0], 0)['records'][0]['original']['content'] == 'DISPOSABLE_TRANSFER_CANARY'
            finally:
                await transfers.close()
        finally:
            await archive.close()
    finally:
        await museum.close()
    print('PASS: storage round trip, closed registration, durable evidence/tombstones/transfer queue, and setup access control.')


if __name__ == '__main__':
    asyncio.run(main())
