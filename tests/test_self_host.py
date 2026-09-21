"""The starter must not attach existing encrypted state to a fresh database."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('bindings,expected', [
    ('', 1),
    ('MEMORY_STATE_VOLUME=litemem_litemem-state\n', 1),
    ('COMPOSE_PROJECT_NAME=litemem\nMEMORY_STATE_VOLUME=litemem_litemem-state\n'
     'MEMORY_DB_NAME=litemem\nMEMORY_DB_USER=litemem\n', 0),
])
def test_starter_requires_complete_legacy_bindings(tmp_path, bindings, expected):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/self-host.sh', scripts / 'self-host.sh')
    (tmp_path / '.env.selfhost').write_text(bindings + 'POSTGRES_PASSWORD=synthetic-test-only\n')
    tools = tmp_path / 'bin'
    tools.mkdir()
    docker = tools / 'docker'
    # Simulate detection of the old state volume; never invoke real Docker.
    docker.write_text('#!/bin/sh\nif [ "$1" = volume ]; then exit 0; fi\necho "$*" >> docker-actions\n')
    docker.chmod(0o700)
    result = subprocess.run(['sh', str(scripts / 'self-host.sh')], cwd=tmp_path,
                            env={**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']},
                            capture_output=True, text=True)
    assert result.returncode == expected
    actions = tmp_path / 'docker-actions'
    if expected:
        assert not actions.exists()
        assert 'docs/upgrading.md' in result.stderr
    else:
        assert 'up --build --remove-orphans --wait' in actions.read_text()
