import pytest

from litellm_memory.crypto import KeyVault
from litellm_memory.storage import Storage


@pytest.mark.asyncio
async def test_legacy_plaintext_migration_cleans_database_and_wal_and_survives_reopen(tmp_path):
    path = tmp_path / 'jobs.sqlite3'
    old = Storage(path)
    await old.open()
    job_id = await old.enqueue('OWNER', 'web', {'question': 'PRIVATE_MIGRATION_CANARY'})
    await old.setting('credential', 'PRIVATE_SETTINGS_CANARY')
    await old.remember('session', 'PRIVATE_HISTORY_CANARY', 'PRIVATE_ANSWER_CANARY')
    await old.close()
    vault = KeyVault(tmp_path)
    current = Storage(path, vault)
    await current.open()
    assert 'PRIVATE_MIGRATION_CANARY' in (await current.get_job(job_id))['payload']
    assert await current.setting('credential') == 'PRIVATE_SETTINGS_CANARY'
    assert (await current.history('session'))[0]['content'] == 'PRIVATE_HISTORY_CANARY'
    for file in tmp_path.glob('jobs.sqlite3*'):
        assert b'PRIVATE_' not in file.read_bytes()
    await current.close()
    reopened = Storage(path, vault)
    await reopened.open()
    assert await reopened.setting('credential') == 'PRIVATE_SETTINGS_CANARY'
    await reopened.close()
