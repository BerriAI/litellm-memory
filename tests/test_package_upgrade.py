"""Upgrade boundaries: installed commands and existing state survive the rename."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_model_settings import application

from litellm_memory.connector import LocalCapture
from litellm_memory.storage import Storage


@pytest.mark.parametrize('module', ['connector', 'transfer_cli', 'evaluation', 'imports'])
def test_installed_module_commands_still_launch(module, tmp_path):
    for package in ['litellm_memory', 'moegent']:
        result = subprocess.run([sys.executable, '-m', f'{package}.{module}', '--help'],
                                cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert 'LiteLLM Memory' in result.stdout


def test_legacy_launch_paths_resolve_to_current_source():
    for module in ['app', 'application', 'boot', 'connector', 'observability']:
        canonical = importlib.util.find_spec(f'litellm_memory.{module}')
        legacy = importlib.util.find_spec(f'moegent.{module}')
        assert canonical.origin == legacy.origin
        assert Path(canonical.origin).parent.name == 'litellm_memory'


def test_old_command_reads_existing_encrypted_capture_queue(tmp_path):
    capture = LocalCapture(tmp_path)
    try:
        capture.enqueue({'source_id': 'existing', 'content': 'PRIVATE_RENAME_CANARY'})
        capture.db.commit()
    finally:
        capture.close()
    for package in ['moegent', 'litellm_memory']:
        result = subprocess.run([sys.executable, '-m', f'{package}.connector',
                                 '--directory', str(tmp_path), 'status'],
                                cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['queued'] == 1
        assert 'PRIVATE_RENAME_CANARY' not in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize('also_has_new_file', [False, True])
async def test_existing_jobs_and_settings_survive_package_upgrade(tmp_path, also_has_new_file):
    legacy_path = tmp_path / 'moegent.sqlite3'
    old = Storage(legacy_path)
    await old.open()
    try:
        job_id = await old.enqueue('T:OWNER', 'web', {'question': 'PRIVATE_RENAME_CANARY'})
        await old.setting('upgrade-proof', 'saved-setting')
    finally:
        await old.close()
    canonical_path = tmp_path / 'litellm-memory.sqlite3'
    if also_has_new_file:
        canonical_path.touch()
    for _ in range(2):
        app = application(tmp_path, [])
        with TestClient(app) as client:
            assert app.state.store.path == legacy_path
            job = client.portal.call(app.state.store.get_job, job_id)
            assert json.loads(job['payload'])['question'] == 'PRIVATE_RENAME_CANARY'
            assert client.portal.call(app.state.store.setting, 'upgrade-proof') == 'saved-setting'
            assert client.get('/health').status_code == 200
        assert b'PRIVATE_RENAME_CANARY' not in legacy_path.read_bytes()
    assert canonical_path.exists() == also_has_new_file
    if also_has_new_file:
        assert canonical_path.stat().st_size == 0


def test_new_install_uses_canonical_database(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert app.state.store.path == tmp_path / 'litellm-memory.sqlite3'
    assert (tmp_path / 'litellm-memory.sqlite3').exists()
    assert not (tmp_path / 'moegent.sqlite3').exists()
