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
