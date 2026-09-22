from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from test_model_settings import application, enroll

from litellm_memory.processing import process_record


@pytest.mark.parametrize('private_topic', [False, True])
def test_agent_memory_is_indexed_verbatim_without_another_generation_call(tmp_path, private_topic):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        app.state.settings.sharing_enabled = app.state.archive.sharing_enabled = True
        if private_topic:
            client.portal.call(app.state.archive.add_rule, 'T:OWNER', 'Keep authentication work private')
        text = 'Investigated the onboarding callback. The first memory was saved; browser handoff still needs work.'
        saved = client.post('/api/v1/memories', json={'source_id': 'agent', 'content': text,
                            'metadata': {'evidence_type': 'agent_memory'}}).json()
        app.state.gateway.generate = AsyncMock(side_effect=AssertionError('Must not rewrite agent memory'))
        row = client.portal.call(app.state.archive.next_work)
        client.portal.call(process_record, app.state.archive, app.state.gateway, row)
        result = client.get('/api/v1/memories/' + saved['id']).json()
        assert result['status'] == 'ready'
        assert result['content'] == result['summary'] == result['original']['content'] == text
        app.state.gateway.generate.assert_not_called()
        preview = client.get('/api/v1/memories/' + saved['id'] + '/sharing-preview').json()
        assert preview['can_share'] is (not private_topic)
        assert preview['summary'] == text
        if private_topic:
            assert 'topic you chose to keep private' in preview['notice']


@pytest.mark.parametrize('failed', [False, True])
def test_sharing_reports_processing_cause_without_unrelated_privacy_reasons(tmp_path, failed):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        app.state.settings.sharing_enabled = app.state.archive.sharing_enabled = True
        saved = client.post('/api/v1/memories', json={'source_id': 'pending', 'content': 'Work'}).json()
        if failed:
            async def fail():
                await app.state.archive.db.execute("UPDATE records SET status='failed' WHERE id=?", (saved['id'],))
                await app.state.archive.db.commit()
            client.portal.call(fail)
        preview = client.get('/api/v1/memories/' + saved['id'] + '/sharing-preview').json()
        assert not preview['can_share']
        assert 'processing failed' in preview['notice'] if failed else 'when processing finishes' in preview['notice']
        assert 'private topic' not in preview['notice'] and 'sharing types' not in preview['notice']


def seed_sharing(client, app):
    from types import SimpleNamespace

    archive = app.state.archive
    enroll(client, app)
    app.state.settings.sharing_enabled = archive.sharing_enabled = True
    rules = [client.portal.call(archive.add_rule, 'T:OWNER', text) for text in
             ['Customer details', 'Contract discussions', 'Unrelated topic']]

    async def classify(text, questions):
        return SimpleNamespace(model='jev-test', answers={key: SimpleNamespace(
            noul=0.9 if 'restricted' in text and 'Unrelated' not in instruction else 0.2)
            for key, instruction in questions.items()})

    app.state.gateway.classify = AsyncMock(side_effect=classify)
    ids = {}
    for label, historical in [('eligible', False), ('historical', True), ('filtered', False),
                              ('pending', False), ('failed', False)]:
        saved = client.post('/api/v1/memories', json={
            'source_id': label, 'title': label, 'historical': historical,
            'content': 'restricted work' if label == 'filtered' else 'Ordinary work',
            'metadata': {'evidence_type': 'agent_memory'},
        }).json()
        ids[label] = saved['id']
        if label not in {'pending', 'failed'}:
            row = client.portal.call(archive.next_work, historical)
            client.portal.call(process_record, archive, app.state.gateway, row)
    async def fail():
        await archive.db.execute("UPDATE records SET status='failed' WHERE id=?", (ids['failed'],))
        await archive.db.commit()
    client.portal.call(fail)
    return ids, rules


def test_filtered_reasons_are_cached_owner_only_current_and_threshold_consistent(tmp_path):
    from test_model_settings import session

    app = application(tmp_path, [])
    with TestClient(app) as client:
        ids, rules = seed_sharing(client, app)
        app.state.gateway.classify.reset_mock()
        path = '/api/v1/memories/' + ids['filtered'] + '/privacy-matches'
        result = client.get(path)
        assert result.status_code == 200
        assert result.json()['rules'] == [{'id': r['id'], 'instruction': r['instruction']} for r in rules[:2]]
        assert client.get('/api/v1/memories/' + ids['eligible'] + '/privacy-matches').json()['rules'] == []
        feed = client.post('/api/v1/memories/search', json={}).json()['results']
        flags = {r['id']: r.get('privacy_blocked') for r in feed}
        assert flags[ids['filtered']] is True and flags[ids['eligible']] is False
        assert flags[ids['pending']] is None
        assert client.get(path, headers={'Authorization': 'Bearer anything'}).status_code == 403
        enroll(client, app, 'COLLEAGUE')
        assert client.get(path).status_code == 404
        assert client.post('/api/v1/memories/search', json={}).json()['results'] == []
        session(client)
        client.portal.call(app.state.archive.remove_rule, 'T:OWNER', rules[0]['id'])
        assert client.get(path).json()['rules'] == []  # Pending recheck, never stale explanations.
        row = client.portal.call(app.state.archive.row, ids['filtered'])
        # Process all live records; deletion of a rule reuses the other cached results.
        while row := client.portal.call(app.state.archive.next_work):
            client.portal.call(process_record, app.state.archive, app.state.gateway, row)
        assert client.get(path).json()['rules'] == [{'id': rules[1]['id'], 'instruction': rules[1]['instruction']}]
        # Reads never classify. Existing records reuse cached results; only the
        # two originally unfinished records need classification during processing.
        assert app.state.gateway.classify.await_count == 2


def test_share_existing_reviews_all_history_without_sharing_filtered_or_unfinished_records(tmp_path):
    from test_model_settings import session

    from litellm_memory.archive import Reader

    app = application(tmp_path, [])
    with TestClient(app) as client:
        ids, _ = seed_sharing(client, app)
        preferences = client.get('/api/v1/sharing-preferences').json()
        enroll(client, app, 'COLLEAGUE')
        other = client.post('/api/v1/memories', json={'source_id': 'other', 'content': 'Other private work'}).json()
        session(client)
        preview = client.get('/api/v1/sharing/preview-existing').json()
        assert preview['counts'] == dict(eligible=2, filtered=1, processing=1, failed=1, excluded=0)
        assert client.post('/api/v1/sharing/existing', json={'revision': preview['revision']}).json()['queued'] == 2
        # Confirmation queues work; it does not bypass the worker's privacy gate.
        for label, record_id in ids.items():
            row = client.portal.call(app.state.archive.row, record_id)
            assert row['visibility'] == 'private'
            assert (row['intent'] == 'public') is (label in {'eligible', 'historical'})
        assert client.portal.call(app.state.archive.row, other['id'])['intent'] != 'public'
        assert client.get('/api/v1/sharing-preferences').json() == preferences
        app.state.gateway.classify.reset_mock()
        # Process just the two explicitly queued memories.
        for historical in [False, True]:
            row = client.portal.call(app.state.archive.next_work, historical)
            assert row['id'] == ids['historical' if historical else 'eligible']
            client.portal.call(process_record, app.state.archive, app.state.gateway, row)
        app.state.gateway.classify.assert_not_called()
        for label in ['eligible', 'historical']:
            public = client.portal.call(app.state.archive.read, Reader('T:COLLEAGUE'), ids[label])
            assert public['visibility'] == 'public' and public['content'] == 'Ordinary work'
            assert 'privacy_blocked' not in public and public['metadata'] == {}
        assert client.portal.call(app.state.archive.row, ids['filtered'])['visibility'] == 'private'
        retry = client.post('/api/v1/sharing/existing', json={'revision': preview['revision']})
        assert retry.status_code == 400 and 'Review' in retry.json()['detail']


@pytest.mark.parametrize('change', ['rule', 'capture', 'visibility', 'delete'])
def test_bulk_confirmation_rejects_a_changed_preview(tmp_path, change):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        ids, _ = seed_sharing(client, app)
        preview = client.get('/api/v1/sharing/preview-existing').json()
        if change == 'rule':
            client.portal.call(app.state.archive.add_rule, 'T:OWNER', 'Another private topic')
        elif change == 'capture':
            client.post('/api/v1/memories', json={'source_id': 'new', 'content': 'New work'})
        elif change == 'visibility':
            client.put('/api/v1/memories/' + ids['eligible'] + '/visibility', json={'visibility': 'private'})
        else:
            client.delete('/api/v1/memories/' + ids['eligible'])
        response = client.post('/api/v1/sharing/existing', json={'revision': preview['revision']})
        assert response.status_code == 400
        assert client.portal.call(app.state.archive.row, ids['historical'])['intent'] != 'public'


def test_bulk_sharing_requires_browser_company_sharing_and_respects_legacy_restrictions(tmp_path):
    from test_model_settings import session

    app = application(tmp_path, [])
    with TestClient(app) as client:
        ids, _ = seed_sharing(client, app)
        preview = client.get('/api/v1/sharing/preview-existing').json()
        assert client.post('/api/v1/sharing/existing', json=preview,
                           headers={'Authorization': 'Bearer anything'}).status_code == 403
        assert client.post('/api/v1/sharing/existing', json=preview,
                           headers={'Origin': 'https://other.example'}).status_code == 403
        enroll(client, app, 'COLLEAGUE')
        assert client.post('/api/v1/sharing/existing', json=preview).status_code == 400
        session(client)
        app.state.settings.sharing_enabled = app.state.archive.sharing_enabled = False
        assert client.post('/api/v1/sharing/existing', json=preview).status_code == 403
        app.state.settings.sharing_enabled = app.state.archive.sharing_enabled = True
        client.portal.call(app.state.archive.set_sharing_preferences, 'T:OWNER', [], False)
        for historical in [False, True]:
            while row := client.portal.call(app.state.archive.next_work, historical):
                client.portal.call(process_record, app.state.archive, app.state.gateway, row)
        blocked = client.get('/api/v1/sharing/preview-existing').json()
        assert blocked['counts']['eligible'] == 0 and blocked['counts']['excluded'] == 4
        assert client.post('/api/v1/sharing/existing', json=blocked).json()['queued'] == 0
        assert client.portal.call(app.state.archive.row, ids['eligible'])['visibility'] == 'private'


def test_rule_added_after_bulk_confirmation_still_blocks_publication(tmp_path):
    app = application(tmp_path, [])
    with TestClient(app) as client:
        ids, _ = seed_sharing(client, app)
        preview = client.get('/api/v1/sharing/preview-existing').json()
        client.post('/api/v1/sharing/existing', json=preview).raise_for_status()
        client.portal.call(app.state.archive.add_rule, 'T:OWNER', 'Everything')
        from types import SimpleNamespace
        app.state.gateway.classify = AsyncMock(side_effect=lambda text, questions: SimpleNamespace(
            model='jev-test', answers={key: SimpleNamespace(noul=1.0) for key in questions}))
        row = client.portal.call(app.state.archive.next_work)
        client.portal.call(process_record, app.state.archive, app.state.gateway, row)
        assert row['id'] == ids['eligible']
        assert client.portal.call(app.state.archive.row, ids['eligible'])['visibility'] == 'private'


def test_threshold_change_reuses_cached_scores_and_preserves_sharing_intent(tmp_path):
    from types import SimpleNamespace

    app = application(tmp_path, [])
    with TestClient(app) as client:
        enroll(client, app)
        archive = app.state.archive
        app.state.settings.sharing_enabled = archive.sharing_enabled = True
        app.state.settings.jev_private_threshold = 0.2
        client.portal.call(archive.configure_privacy_threshold, 0.2)
        client.portal.call(archive.add_rule, 'T:OWNER', 'PR 17')
        app.state.gateway.classify = AsyncMock(side_effect=lambda text, questions: SimpleNamespace(
            model='jev-test', answers={key: SimpleNamespace(noul=0.52) for key in questions}))
        ids = []
        for name in ['approved', 'private']:
            saved = client.post('/api/v1/memories', json={'source_id': name, 'content': 'Reviewed a salary change.',
                                                        'metadata': {'evidence_type': 'agent_memory'}}).json()
            ids.append(saved['id'])
            if name == 'approved':
                client.put('/api/v1/memories/' + saved['id'] + '/visibility', json={'visibility': 'public'})
            row = client.portal.call(archive.next_work)
            client.portal.call(process_record, archive, app.state.gateway, row)
            assert client.portal.call(archive.row, saved['id'])['visibility'] == 'private'
        app.state.gateway.classify.reset_mock()
        app.state.settings.jev_private_threshold = 0.7
        client.portal.call(archive.configure_privacy_threshold, 0.7)
        for record_id in ids:
            assert client.portal.call(archive.row, record_id)['status'] == 'pending'
            row = client.portal.call(archive.next_work)
            client.portal.call(process_record, archive, app.state.gateway, row)
        app.state.gateway.classify.assert_not_called()
        assert client.portal.call(archive.row, ids[0])['visibility'] == 'public'
        assert client.portal.call(archive.row, ids[1])['visibility'] == 'private'
        # Same cutoff on restart is a no-op.
        client.portal.call(archive.configure_privacy_threshold, 0.7)
        assert client.portal.call(archive.row, ids[0])['status'] == 'ready'
        # Making the rule stricter withdraws the public copy before recalculation.
        client.portal.call(archive.configure_privacy_threshold, 0.5)
        assert client.portal.call(archive.row, ids[0])['visibility'] == 'private'
