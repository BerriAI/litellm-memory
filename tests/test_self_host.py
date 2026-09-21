"""The starter reuses operator credentials and starts the documented stack."""

import os
import shutil
import subprocess
from pathlib import Path


def test_starter_preserves_operator_configuration(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/self-host.sh', scripts / 'self-host.sh')
    original = 'POSTGRES_PASSWORD=synthetic-test-only\nPUBLIC_BASE_URL=https://memory.example\n'
    (tmp_path / '.env.selfhost').write_text(original)
    tools = tmp_path / 'bin'
    tools.mkdir()
    docker = tools / 'docker'
    # Simulate Docker without creating containers.
    docker.write_text('#!/bin/sh\necho "$*" >> docker-actions\n')
    docker.chmod(0o700)
    result = subprocess.run(['sh', str(scripts / 'self-host.sh')], cwd=tmp_path,
                            env={**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']},
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert (tmp_path / '.env.selfhost').read_text() == original
    assert 'up --build --remove-orphans --wait' in (tmp_path / 'docker-actions').read_text()
