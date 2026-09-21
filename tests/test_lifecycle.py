import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_archive import archive as archive_fixture
from test_model_settings import application, enroll

from litellm_memory.archive import Reader, WorkInput
from litellm_memory.config import Settings
from litellm_memory.memory_slack import MemorySlack

archive = archive_fixture


@pytest.mark.asyncio
async def test_correction_retries_preserve_one_record_date_and_relation(archive):
    original = await archive.capture('T1:A', WorkInput(source_id='retry-original', content='Original'))
    request_id = uuid4()
    stamp = '2026-09-20T17:04:01+00:00'
    args = ('T1:A', original['id'], 'Corrected once')
    first, second = await asyncio.gather(
        archive.correct(*args, request_id=request_id, occurred_at=stamp),
        archive.correct(*args, request_id=request_id, occurred_at=stamp))
    assert first['id'] == second['id']
    assert sorted([first['duplicate'], second['duplicate']]) == [False, True]
    saved = await archive.read(Reader('T1:A'), first['id'])
    assert saved['original']['occurred_at'] == '2026-09-20T17:04:01Z'
    assert len((await archive.read(Reader('T1:A'), original['id']))['revisions']) == 1
    with pytest.raises(ValueError, match='cannot replace'):
        await archive.correct('T1:A', original['id'], 'Changed on retry', request_id=request_id, occurred_at=stamp)
    # An idempotency key never aliases a correction to another original.
    other = await archive.capture('T1:A', WorkInput(source_id='other-original', content='Other'))
    different = await archive.correct('T1:A', other['id'], 'Corrected once', request_id=request_id, occurred_at=stamp)
    assert different['id'] != first['id']


def test_correction_api_validates_retry_identity_and_accepts_exact_replay(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        saved = client.post('/api/memories', json={'source_id': 'original', 'content': 'Original'}).json()
        endpoint = '/api/memories/' + saved['id'] + '/corrections'
        payload = {'content': 'Correction', 'request_id': str(uuid4())}
        assert client.post(endpoint, json=payload).status_code == 400
        payload['occurred_at'] = '2026-09-20T17:04:01Z'
        first = client.post(endpoint, json=payload)
        replay = client.post(endpoint, json=payload)
        assert first.status_code == replay.status_code == 200
        assert first.json()['id'] == replay.json()['id']
        assert replay.json()['duplicate'] is True


@pytest.mark.asyncio
async def test_correction_preserves_source_withdraws_publication_and_restores_links_in_either_order(archive):
    source = WorkInput(source_id='original', content='Exact original.  \n')
    first = await archive.capture('T1:A', source)
    await archive.set_visibility('T1:A', first['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(summary='Approved original', labels={'progress': 1})
    await archive.complete_work(row, payload, private_match=False)
    original_blob = archive.museum.entities[first['id'] + ':source']
    with pytest.raises(PermissionError):
        await archive.correct('T1:B', first['id'], 'Not my evidence')
    await archive.set_sharing_preferences('T1:A', ['progress'], True)
    correction = await archive.correct('T1:A', first['id'], 'The earlier result was wrong.')
    newer = await archive.row(correction['id'])
    assert newer['intent'] == 'private'
    assert not await archive.publication_allowed(newer, {'labels': {'progress': 1}})
    assert archive.museum.entities[first['id'] + ':source'] == original_blob
    original = await archive.read(Reader('T1:A'), first['id'])
    assert original['original'] == source.model_dump(mode='json')
    assert original['revisions'][0]['record_id'] == correction['id']
    with pytest.raises(LookupError):
        await archive.read(Reader('T1:B'), first['id'])
    # Rebuild the relationship from originals in export order (newest first).
    correction_source = (await archive.read(Reader('T1:A'), correction['id']))['original']
    await archive.db.execute('DELETE FROM record_relations')
    await archive.db.execute('DELETE FROM records')
    await archive.db.commit()
    await archive.capture('T1:A', WorkInput.model_validate(correction_source).model_copy(update={'historical': True}))
    await archive.capture('T1:A', source.model_copy(update={'historical': True}))
    assert (await archive.read(Reader('T1:A'), first['id']))['revisions'][0]['record_id'] == correction['id']
    await archive.delete('T1:A', correction['id'])
    assert not (await archive.read(Reader('T1:A'), first['id']))['revisions']


@pytest.mark.asyncio
async def test_leave_rejoin_retains_private_key_but_never_reactivates_grants(archive):
    first = await archive.capture('T1:A', WorkInput(source_id='one', content='PRIVATE_CANARY'))
    key = await archive.key('T1:A')
    token = await archive.create_token('T1:A')
    await archive.set_sharing_preferences('T1:A', ['progress'], True)
    await archive.leave('T1:A')
    with pytest.raises(ValueError):
        await archive.enroll('T1:A', {})
    with pytest.raises(LookupError):
        await archive.read(Reader('T1:A'), first['id'])
    await archive.rejoin('T1:A', {'name': 'Alice'})
    assert await archive.key('T1:A') == key
    assert (await archive.read(Reader('T1:A'), first['id']))['content'] == 'PRIVATE_CANARY'
    assert not (await archive.member('T1:A'))['capture_enabled']
    assert not (await archive.sharing_preferences('T1:A'))['auto_share']
    assert await archive.token_owner(token['token']) is None
    assert (await archive.row(first['id']))['intent'] == 'private'


@pytest.mark.asyncio
async def test_slack_fresh_membership_and_shared_audience_fail_closed(archive):
    settings = Settings(_env_file=None, slack_team_id='T1', slack_bot_token='fake', slack_bot_user_id='BOT',
                        registration_mode='domain', allowed_email_domain='berri.ai', allowed_slack_users='A,B')
    store = AsyncMock()
    store.setting.return_value = None
    store.channel_allowed.return_value = True
    slack = MemorySlack(settings, store, archive)
    assert not slack.explicitly_ineligible({'id': 'A', 'team_id': 'T1', 'profile': {'display_name': 'New name'}})
    assert slack.explicitly_ineligible({'id': 'A', 'deleted': True})
    assert slack.explicitly_ineligible({'id': 'A', 'profile': {'email': 'a@another.example'}})
    users = {u: {'id': u, 'team_id': 'T1', 'profile': {'email': u + '@berri.ai'}} for u in ['A', 'B', 'C']}
    channel = {'is_im': True, 'user': 'A'}
    participants = ['A', 'B', 'BOT']
    async def api(method, body):
        if method == 'users.info':
            return {'user': users[body['user']]}
        if method == 'conversations.info':
            return {'channel': channel}
        if method == 'conversations.members':
            return {'members': participants}
        raise AssertionError(method)
    slack.api = api
    payload = {'team': 'T1', 'user': 'A', 'channel': 'D1'}
    assert await slack.reader(payload) == Reader('T1:A')
    channel = {'is_private': True, 'is_im': False}
    assert await slack.reader(payload) == Reader('T1:A', 'public')
    for invalid in [{'is_ext_shared': True}, {'is_org_shared': True}, {'is_shared': True}]:
        channel = invalid
        with pytest.raises(PermissionError):
            await slack.reader(payload)
    channel = {}
    participants.append('C')
    with pytest.raises(PermissionError):
        await slack.reader(payload)
    participants.remove('C')
    for field, value in [('is_restricted', True), ('deleted', True), ('team_id', 'OTHER')]:
        users['B'][field] = value
        with pytest.raises(PermissionError):
            await slack.reader(payload)
        users['B'].pop(field)
        users['B']['team_id'] = 'T1'
    users['B']['profile']['email'] = 'someone@notberri.ai'
    with pytest.raises(PermissionError):
        await slack.reader(payload)
    token = await archive.create_token('T1:A')
    archive.check_eligibility = slack.verify_member
    users['A']['deleted'] = True
    assert await slack.verify_member('T1:A')  # Short cache for dashboard/API traffic.
    assert not await slack.verify_member('T1:A', fresh=True)
    assert await archive.token_owner(token['token']) is None
    assert await archive.member('T1:A') is None


def test_rejoin_and_correction_api_require_own_browser_identity(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        saved = client.post('/api/memories', json={'source_id': 'one', 'content': 'Original'}).json()
        response = client.post('/api/memories/' + saved['id'] + '/corrections', json={'content': 'Updated'})
        assert response.status_code == 200, response.text
        token = client.portal.call(app.state.archive.create_token, 'T:OWNER')['token']
        assert client.post('/api/memories/' + saved['id'] + '/corrections', json={'content': 'Token edit'},
                           headers={'Authorization': 'Bearer ' + token}).status_code == 403
        client.portal.call(app.state.archive.leave, 'T:OWNER')
        assert client.get('/api/me').json()['can_rejoin']
        assert client.post('/api/rejoin', json={}).status_code == 200
        assert client.get('/api/memories/' + saved['id']).status_code == 200
        assert client.post('/api/memories', json={'source_id': 'two', 'content': 'Capture paused'}).status_code == 403
