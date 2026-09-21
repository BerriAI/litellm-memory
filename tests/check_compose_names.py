"""Check state bindings before CI creates any deployment containers."""

import json
import os
import subprocess


def config(project, **bindings):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('COMPOSE_', 'MEMORY_'))}
    env.update(POSTGRES_PASSWORD='disposable-config-check', **bindings)
    return json.loads(subprocess.check_output(
        ['docker', 'compose', '--env-file', '/dev/null', '-p', project, 'config', '--format', 'json'], env=env))


def main():
    primary = config('litellm-memory')
    restored = config('litellm-memory-restored')
    for volume in ('litellm-memory-state', 'postgres-data'):
        assert primary['volumes'][volume]['name'] != restored['volumes'][volume]['name'], volume
    assert primary['services']['postgres']['environment']['POSTGRES_DB'] == 'litellm_memory'
    assert 'litellm-memory' in primary['services']
    legacy = config('litemem', MEMORY_STATE_VOLUME='litemem_litemem-state',
                    MEMORY_DB_NAME='litemem', MEMORY_DB_USER='litemem')
    assert legacy['volumes']['litellm-memory-state']['name'] == 'litemem_litemem-state'
    assert legacy['volumes']['postgres-data']['name'] == 'litemem_postgres-data'
    assert legacy['services']['postgres']['environment']['POSTGRES_DB'] == 'litemem'
    assert legacy['services']['postgres']['environment']['POSTGRES_USER'] == 'litemem'
    assert legacy['services']['litellm-memory']['environment']['DATABASE_URL'].startswith('postgresql://litemem:')
    print('Fresh, restored and legacy deployment bindings verified.')


if __name__ == '__main__':
    main()
