import json
from types import SimpleNamespace

import pytest
from test_archive import archive as archive_fixture

from moegent.archive import Reader, WorkInput
from moegent.work_views import ViewRequest, WorkViews

archive = archive_fixture


class ViewGateway:
    def __init__(self):
        self.inputs = []
        self.fabricate = False

    def snapshot(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, **kwargs):
        self.inputs.append(json.loads(messages[-1]['content']))
        source = self.inputs[-1]['evidence'][0]
        return json.dumps({'items': [{'text': source['summary'], 'kind': 'decision',
                          'sources': ['fake-id' if self.fabricate else source['id']]}]})


async def ready(archive, name, text, when, *, public=False):
    saved = await archive.capture('T1:A', WorkInput(source_id=name, content=text, occurred_at=when))
    if public:
        await archive.set_visibility('T1:A', saved['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(summary=text, projects=['Project A'], labels={'decision': 0.95})
    await archive.complete_work(row, payload, private_match=False)
    return saved['id']


@pytest.mark.asyncio
async def test_views_keep_private_sources_out_of_public_and_do_not_regenerate_unchanged(archive):
    private = await ready(archive, 'private', 'PRIVATE_CANARY', '2026-09-19T01:00:00Z')
    shared = await ready(archive, 'shared', 'Approved company update', '2026-09-19T02:00:00Z', public=True)
    gateway = ViewGateway()
    views = WorkViews(archive, gateway, SimpleNamespace(values={'timezone': 'UTC'}))
    await views.open()
    response = await views.get(Reader('T1:B'), 'T1:A', ViewRequest())
    assert response['view'] is None and response['audience'] == 'public'
    await views.build(await views.next())
    assert 'PRIVATE_CANARY' not in json.dumps(gateway.inputs)
    result = await views.get(Reader('T1:B'), 'T1:A', ViewRequest())
    assert result['view']['source_ids'] == [shared] and result['stale'] is False
    assert await views.next() is None
    await views.get(Reader('T1:A'), 'T1:A', ViewRequest())
    await views.build(await views.next())
    mine = await views.get(Reader('T1:A'), 'T1:A', ViewRequest())
    assert set(mine['view']['source_ids']) == {private, shared}
    assert 'PRIVATE_CANARY' not in json.dumps(archive.museum.entities)
    with pytest.raises(LookupError):
        await views.get(Reader('T2:C'), 'T1:A', ViewRequest())
    await archive.set_visibility('T1:A', shared, 'private')
    result = await views.get(Reader('T1:B'), 'T1:A', ViewRequest())
    assert result['view'] is None and result['stale']


@pytest.mark.asyncio
async def test_daily_views_use_work_date_timezone_and_report_pending_or_undated(archive):
    before = await ready(archive, 'before', 'Earlier work', '2026-09-19T01:00:00Z')
    await ready(archive, 'next', 'Next day work', '2026-09-19T12:00:00Z')
    await archive.capture('T1:A', WorkInput(source_id='pending', content='Unprocessed', occurred_at='2026-09-19T02:00:00Z'))
    await archive.capture('T1:A', WorkInput(source_id='undated', content='Unknown date'))
    gateway = ViewGateway()
    views = WorkViews(archive, gateway, SimpleNamespace(values={'timezone': 'America/Los_Angeles'}))
    await views.open()
    request = ViewRequest(kind='daily', day='2026-09-18')
    await views.get(Reader('T1:A'), 'T1:A', request)
    await views.build(await views.next())
    view = (await views.get(Reader('T1:A'), 'T1:A', request))['view']
    assert view['source_ids'] == [before]
    assert view['coverage'] == {'total': 2, 'included': 1, 'pending': 1, 'older_not_included': 0, 'undated': 1}
    await archive.delete('T1:A', before)
    assert not any(key.startswith('view:') for key in archive.museum.entities)
    assert (await views.get(Reader('T1:A'), 'T1:A', request))['view'] is None


@pytest.mark.asyncio
async def test_view_rejects_fabricated_citations_and_mid_generation_policy_change(archive):
    await ready(archive, 'one', 'A decision', '2026-09-19T01:00:00Z')
    gateway = ViewGateway()
    gateway.fabricate = True
    views = WorkViews(archive, gateway, SimpleNamespace(values={}))
    await views.open()
    await views.get(Reader('T1:A'), 'T1:A', ViewRequest())
    row = await views.next()
    with pytest.raises(ValueError, match='citation'):
        await views.build(row)
    assert not any(key.startswith('view:') for key in archive.museum.entities)
    gateway.fabricate = False
    generate = gateway.generate
    async def revoke(messages, **kwargs):
        result = await generate(messages, **kwargs)
        await archive.add_rule('T1:A', 'A newly private topic')
        return result
    gateway.generate = revoke
    await views.build(row)
    assert not any(key.startswith('view:') for key in archive.museum.entities)


@pytest.mark.asyncio
async def test_intake_recovery_preserves_ciphertext_and_original_across_retry(archive):
    put = archive.museum.put
    async def outage(*args, **kwargs):
        raise OSError('storage offline')
    archive.museum.put = outage
    body = WorkInput(source_id='outage', content='DURABLE_PRIVATE_CANARY')
    saved = await archive.capture('T1:A', body)
    assert saved['status'] == 'storing'
    source = (await archive.row(saved['id']))['source_payload']
    assert 'DURABLE_PRIVATE_CANARY' not in source
    await archive.add_rule('T1:A', 'Sensitive topic')
    assert (await archive.row(saved['id']))['status'] == 'storing'
    assert await archive.next_work() is None
    archive.museum.put = put
    await archive.persist_intake(saved['id'])
    assert archive.museum.entities[saved['id'] + ':source'] == json.loads(source)
    duplicate = await archive.capture('T1:A', body.model_copy(update={'historical': True}))
    assert duplicate['duplicate']
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload['summary'] = 'A useful summary'
    await archive.complete_work(row, payload, private_match=False)
    assert archive.museum.entities[saved['id'] + ':source'] == json.loads(source)
    await archive.delete('T1:A', saved['id'])
    assert saved['id'] + ':source' not in archive.museum.entities
