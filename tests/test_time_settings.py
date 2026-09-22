from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_archive import archive as archive_fixture
from test_model_settings import application, enroll
from test_setup import claim
from test_work_views import ViewGateway, ready

from litellm_memory.archive import Reader
from litellm_memory.time_settings import ScheduleSettings
from litellm_memory.work_views import ViewRequest, WorkViews

archive = archive_fixture


@pytest.mark.parametrize('zone,at,current,latest,next_run', [
    ('America/Los_Angeles', '02:00', '2026-03-08T09:59:00Z', '2026-03-07', '2026-03-08T10:00:00+00:00'),
    ('America/Los_Angeles', '02:00', '2026-03-08T10:00:00Z', '2026-03-08', '2026-03-09T09:00:00+00:00'),
    ('America/Los_Angeles', '01:30', '2025-11-02T09:15:00Z', '2025-11-02', '2025-11-03T09:30:00+00:00'),
    ('Asia/Kathmandu', '02:00', '2026-09-18T20:15:00Z', '2026-09-19', '2026-09-19T20:15:00+00:00'),
])
def test_schedule_has_one_calendar_occurrence_across_clock_changes(zone, at, current, latest, next_run):
    settings = ScheduleSettings(timezone=zone, daily_time=at)
    current = datetime.fromisoformat(current)
    assert settings.latest(current) == date.fromisoformat(latest)
    assert settings.next_run(current) == next_run


def test_schedule_and_display_settings_are_authorized_private_and_persistent(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        assert client.get('/api/v1/schedule').status_code == 401
        enroll(client, app)
        initial = client.get('/api/v1/schedule').json()
        assert initial['timezone'] == 'UTC' and initial['daily_time'] == '00:00'
        body = {'timezone': 'America/Los_Angeles', 'daily_time': '07:15', 'revision': 0}
        assert client.put('/api/v1/admin/schedule', json=body,
                          headers={'Origin': 'https://elsewhere.example'}).status_code == 403
        changed = client.put('/api/v1/admin/schedule', json=body)
        assert changed.status_code == 200 and changed.json()['revision'] == 1
        assert client.put('/api/v1/admin/schedule', json=body).status_code == 400
        assert client.put('/api/v1/admin/schedule', json={**body, 'revision': 1}).json()['revision'] == 1
        for field, value in [('timezone', '../private'), ('timezone', 'Made/Up'),
                             ('daily_time', '25:00'), ('daily_time', '0١:00')]:
            assert client.put('/api/v1/admin/schedule', json={**body, 'revision': 1, field: value}).status_code == 422
        stamp = client.portal.call(app.state.archive.policy_stamp, Reader('T:OWNER'))
        token = client.portal.call(app.state.archive.create_token, 'T:OWNER')
        headers = {'Authorization': 'Bearer ' + token['token']}
        assert client.put('/api/v1/admin/schedule', json={**body, 'revision': 1}, headers=headers).status_code == 403
        assert client.put('/api/v1/display-preferences', json={'timezone': 'Asia/Tokyo'}, headers=headers).status_code == 403
        assert client.put('/api/v1/display-preferences', json={'timezone': 'Asia/Tokyo'},
                          headers={'Origin': 'https://elsewhere.example'}).status_code == 403
        assert client.put('/api/v1/display-preferences', json={'timezone': 'Asia/Tokyo'}).status_code == 200
        assert client.get('/api/v1/me').json()['display_timezone'] == 'Asia/Tokyo'
        assert stamp == client.portal.call(app.state.archive.policy_stamp, Reader('T:OWNER'))
        assert 'Asia/Tokyo' not in client.get('/api/v1/people').text
        assert 'America/Los_Angeles' not in app.state.deployment.path.read_text()
        enroll(client, app, 'OTHER')
        assert client.put('/api/v1/admin/schedule', json={**body, 'revision': 1}).status_code == 403
        assert client.get('/api/v1/schedule').json()['daily_time'] == '07:15'
        assert client.get('/api/v1/display-preferences').json() == {'timezone': None}
    restarted = application(tmp_path, [])
    with TestClient(restarted) as client:
        enroll(client, restarted)
        assert client.get('/api/v1/schedule').json()['daily_time'] == '07:15'
        assert client.get('/api/v1/display-preferences').json()['timezone'] == 'Asia/Tokyo'
        assert client.put('/api/v1/display-preferences', json={'timezone': None}).json() == {'timezone': None}


def test_setup_rejects_invalid_timezone_before_persisting_it(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        claim(client, app)
        response = client.put('/api/v1/setup/preferences', json={'timezone': 'INVALID_ZONE_CANARY'})
        assert response.status_code == 422 and 'INVALID_ZONE_CANARY' not in response.text
        assert 'timezone' not in app.state.deployment.values
    with pytest.raises(ValidationError):
        ScheduleSettings(timezone='Invalid/Zone')


def test_failed_config_write_preserves_the_active_settings(tmp_path, monkeypatch):
    app = application(tmp_path, [])
    deployment = app.state.deployment
    deployment.save(timezone='UTC', config={'allowed_email_domain': 'berri.ai'})
    before = deployment.path.read_bytes()

    def unavailable(*args):
        raise OSError('Storage unavailable')

    monkeypatch.setattr(deployment, '_atomic', unavailable)
    with pytest.raises(OSError):
        deployment.save(timezone='Asia/Tokyo', config={'allowed_email_domain': 'example.com'})
    assert deployment.values['timezone'] == 'UTC'
    assert deployment.values['settings']['allowed_email_domain'] == 'berri.ai'
    assert app.state.settings.allowed_email_domain == 'berri.ai'
    assert deployment.path.read_bytes() == before


@pytest.mark.asyncio
async def test_generated_archive_keeps_its_original_timezone_after_settings_change(archive):
    saved = await ready(archive, 'late-evening', 'A late decision', '2026-09-19T01:00:00Z')
    deployment = SimpleNamespace(values={'timezone': 'America/Los_Angeles'})
    gateway = ViewGateway()
    views = WorkViews(archive, gateway, deployment)
    await views.open()
    request = ViewRequest(kind='daily', day='2026-09-18')
    old = await views.get(Reader('T1:A'), 'T1:A', request, enqueue=True)
    row = await views.next()
    deployment.values['timezone'] = 'UTC'
    await views.build(row)
    current = await views.get(Reader('T1:A'), 'T1:A', request)
    assert current['id'] == old['id'] and current['timezone'] == 'America/Los_Angeles'
    assert await views.next() is None
    deployment.values['timezone'] = 'America/Los_Angeles'
    previous = await views.get(Reader('T1:A'), 'T1:A', request)
    assert previous['id'] == old['id'] and previous['view']['source_ids'] == [saved]
    assert len(gateway.inputs) == 1


@pytest.mark.asyncio
async def test_late_work_waits_for_the_next_midnight_and_reads_never_regenerate(archive):
    archive.sharing_enabled = False
    first = await ready(archive, 'first', 'A decision', '2026-09-18T20:00:00Z')
    gateway = ViewGateway()
    views = WorkViews(archive, gateway, SimpleNamespace(values={'timezone': 'UTC'}))
    await views.open()
    request = ViewRequest(kind='daily', day='2026-09-18')
    await views.schedule(datetime(2026, 9, 19, tzinfo=UTC))
    await views.build(await views.next())
    late = await ready(archive, 'late', 'Late decision', '2026-09-18T22:00:00Z')
    view = await views.get(Reader('T1:A'), 'T1:A', request)
    assert not view['stale'] and view['view']['source_ids'] == [first]
    assert await views.next() is None and len(gateway.inputs) == 1
    await views.open()  # A restart must not run the daily schedule twice.
    await views.schedule(datetime(2026, 9, 19, 18, tzinfo=UTC))
    assert await views.next() is None
    await views.schedule(datetime(2026, 9, 20, tzinfo=UTC))
    # The previous generated summary stays readable while the nightly update runs.
    assert (await views.get(Reader('T1:A'), 'T1:A', request))['view']['source_ids'] == [first]
    await views.build(await views.next())
    assert set((await views.get(Reader('T1:A'), 'T1:A', request))['view']['source_ids']) == {first, late}
    assert len(gateway.inputs) == 2


@pytest.mark.asyncio
async def test_scheduled_jobs_coalesce_missed_days_and_resume_without_duplicate_generation(archive):
    archive.sharing_enabled = False
    await ready(archive, 'yesterday', 'A decision', '2026-03-07T20:00:00Z')
    gateway = ViewGateway()
    views = WorkViews(archive, gateway, SimpleNamespace(values={'timezone': 'America/Los_Angeles'}))
    await views.open()

    async def drain():
        while row := await views.next():
            await views.build(row)

    await views.schedule(datetime(2026, 3, 8, 7, 59, tzinfo=UTC))
    await drain()
    assert len(gateway.inputs) == 0  # March 7 is not yet due; no empty days are generated.
    await views.schedule(datetime(2026, 3, 8, 8, tzinfo=UTC))
    await drain()
    assert len(gateway.inputs) == 1  # March 7 is ready to summarize at local midnight.
    await views.open()  # Startup reconciliation with durable queue state retained.
    await views.schedule(datetime(2026, 3, 8, 10, 5, tzinfo=UTC))
    assert await views.next() is None and len(gateway.inputs) == 1
    await ready(archive, 'missed-day', 'Work during downtime', '2026-03-09T20:00:00Z')
    await views.schedule(datetime(2026, 3, 20, 12, tzinfo=UTC))
    rows = await (await archive.db.execute("SELECT request FROM work_views WHERE owner='T1:A'")).fetchall()
    requests = [archive.vault.open_operational(row['request']) for row in rows]
    assert {request['day'] for request in requests if request['kind'] == 'daily'} == {
        '2026-03-07', '2026-03-09'}


def test_date_filters_and_chronology_compare_instants_across_offsets(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        originals = [('evening', '2026-09-18T23:30:00-07:00'), ('next-day', '2026-09-19T07:00:00Z'),
                     ('morning', '2026-09-19T01:00:00+09:00'), ('unknown-zone', '2026-09-18T12:00:00')]
        identities = {}
        for source, timestamp in originals:
            identities[source] = client.post('/api/v1/memories', json={
                'source_id': source, 'title': source, 'content': source, 'occurred_at': timestamp}).json()['id']
        body = {'created_from': '2026-09-18', 'created_before': '2026-09-19',
                'date_timezone': 'America/Los_Angeles', 'mode': 'keywords'}
        found = client.post('/api/v1/memories/search', json=body).json()
        assert [item['id'] for item in found['results']] == [identities['evening'], identities['morning']]
        # A timezone-unknown original remains preserved, but cannot be placed inside an exact interval.
        assert client.get('/api/v1/memories/' + identities['unknown-zone']).json()['content'] == 'unknown-zone'
        assert client.post('/api/v1/memories/search', json={**body, 'date_timezone': 'Invalid/Zone'}).status_code == 422
        assert client.post('/api/v1/memories/search', json={**body, 'created_from': 'not-a-date'}).status_code == 400
        assert client.post('/api/v1/memories/search', json={**body, 'created_from': '2026-09-20'}).status_code == 422
