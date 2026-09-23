import json
import subprocess
import sys

import httpx
import pytest

from litellm_memory.boot import BootError, database_config, museum_config, provision_account


def test_launcher_does_not_load_the_workers_model_sdk():
    # Boot remains resident while uvicorn runs in another process. Importing
    # LiteLLM in both exhausted the production container's 512 MiB limit.
    subprocess.run([sys.executable, '-c', (
        'import sys; import litellm_memory.boot; '
        'assert "liteagents" not in sys.modules; assert "litellm" not in sys.modules'
    )], check=True, timeout=30)


def test_museum_boot_configuration_is_private_and_hardened_after_bootstrap():
    private = {'encryption': 'test', 'hash': 'hash', 'jwt': 'jwt', 'email': 'service@example.internal', 'ott': 'temporary'}
    first = museum_config('postgresql://user:p%40ss@postgres:5432/memory?sslmode=disable', private, bootstrap=True)
    assert first['db']['password'] == 'p@ss' and first['db']['sslmode'] == 'disable'
    assert first['http']['port'] == 9080
    assert first['internal']['hardcoded-ott']['emails'] == ['service@example.internal,temporary']
    later = museum_config('postgresql://user:password@postgres/memory', private)
    assert later['internal']['disable-registration'] is True
    assert 'hardcoded-ott' not in later['internal'] and later['log-level'] == 'warn'
    assert later['db']['sslmode'] == 'require'
    with pytest.raises(BootError):
        database_config('https://example.com')


@pytest.mark.asyncio
async def test_bundled_account_provisioning_requests_and_consumes_ott_without_leaking_errors():
    requests = []
    def handler(request):
        requests.append(request.url.path)
        data = json.loads(request.content)
        if request.url.path.endswith('/ott'):
            assert data['purpose'] == 'signup'
            return httpx.Response(200)
        assert data['ott'] == 'temporary'
        return httpx.Response(200, json={'token': 'private-service-token'})
    token = await provision_account('http://museum:9080', {'email': 'service@example.internal', 'ott': 'temporary'},
                                    httpx.MockTransport(handler))
    assert token == 'private-service-token'
    assert requests == ['/users/ott', '/users/verify-email']
    with pytest.raises(BootError) as error:
        await provision_account('http://museum:9080', {'email': 'service@example.internal', 'ott': 'temporary'},
                                httpx.MockTransport(lambda r: httpx.Response(500, text='SECRET_RESPONSE')))
    assert 'SECRET_RESPONSE' not in str(error.value)
