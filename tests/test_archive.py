import json

import pytest

from moegent.archive import Archive, Reader, WorkInput
from moegent.crypto import KeyVault


class FakeMuseum:
    def __init__(self):
        self.entities = {}
        self.writes = 0

    async def put(self, entity_id, blob, **kwargs):
        self.entities[entity_id] = blob
        self.writes += 1
        return {"id": entity_id, **blob}

    async def delete(self, entity_id):
        self.entities.pop(entity_id, None)


@pytest.fixture
async def archive(tmp_path):
    archive = Archive(tmp_path / "work.sqlite3", KeyVault(tmp_path), FakeMuseum(), sharing_enabled=True)
    await archive.open()
    await archive.enroll("T1:A", {"name": "Alice"})
    await archive.enroll("T1:B", {"name": "Bob"})
    await archive.enroll("T2:C", {"name": "Other workspace"})
    yield archive
    await archive.close()


@pytest.mark.asyncio
async def test_owner_and_public_channel_boundaries_cover_reads_search_and_other_workspaces(archive):
    capture = await archive.capture("T1:A", WorkInput(source_id="one", content="SECRET launch initiative"))
    record_id = capture["id"]
    assert "SECRET" in (await archive.read(Reader("T1:A"), record_id))["content"]
    for reader in [Reader("T1:B"), Reader("T1:A", "public"), Reader("T2:C")]:
        with pytest.raises(LookupError):
            await archive.read(reader, record_id)
        assert not (await archive.search(reader, "SECRET"))["results"]
    # A processed, explicitly published record is visible only in its own workspace.
    await archive.set_visibility("T1:A", record_id, "public")
    row = await archive.next_work()
    publication = await archive.decode(row)
    publication["summary"] = "An approved public update"
    publication["labels"] = {"progress": 0.95}
    await archive.complete_work(row, publication, private_match=False)
    assert await archive.read(Reader("T1:B"), record_id)
    assert await archive.read(Reader("T1:A", "public"), record_id)
    with pytest.raises(LookupError):
        await archive.read(Reader("T2:C"), record_id)


@pytest.mark.asyncio
async def test_ciphertext_only_persistence_and_immutable_idempotent_import(archive):
    text = "Legacy original. Spaces  and punctuation remain.\n"
    body = WorkInput(source_id="legacy-one", content=text, metadata={"actor": "uploader", "scope": "PRIVATE_SCOPE"},
                     occurred_at="2025-01-02T00:00:00Z", historical=True)
    saved = await archive.capture("T1:A", body)
    again = await archive.capture("T1:A", body)
    assert saved["id"] == again["id"] and again["duplicate"]
    assert archive.museum.writes == 2  # Immutable source plus rebuildable working object.
    with pytest.raises(ValueError):
        await archive.capture("T1:A", body.model_copy(update={"content": "overwrite"}))
    row = await archive.row(saved["id"])
    assert text not in row["payload"] and "PRIVATE_SCOPE" not in row["payload"]
    assert "PRIVATE_SCOPE" not in json.dumps(archive.museum.entities)
    read = await archive.read(Reader("T1:A"), saved["id"])
    assert read["original"]["content"] == text
    assert read["created_at"].startswith("2025-01-02") and read["historical"]
    result = await archive.search(Reader("T1:A"), "PRIVATE_SCOPE")
    assert result["results"][0]["id"] == saved["id"]


@pytest.mark.asyncio
async def test_privacy_rule_hides_previous_public_records_immediately(archive):
    saved = await archive.capture("T1:A", WorkInput(source_id="a", content="Customer research"))
    await archive.db.execute("UPDATE records SET visibility='public' WHERE id=?", (saved["id"],))
    await archive.db.commit()
    await archive.add_rule("T1:A", "Does this contain customer details?")
    with pytest.raises(LookupError):
        await archive.read(Reader("T1:B"), saved["id"])
    assert await archive.read(Reader("T1:A"), saved["id"])


@pytest.mark.asyncio
async def test_auto_sharing_only_applies_to_future_confidently_allowed_types_and_never_imports(archive):
    old = await archive.capture('T1:A', WorkInput(source_id='old', content='Earlier work'))
    await archive.set_sharing_preferences('T1:A', ['progress'], True)
    new = await archive.capture('T1:A', WorkInput(source_id='new', content='Future work'))
    historical = await archive.capture('T1:A', WorkInput(source_id='import', content='Historical work', historical=True))
    progress = {'labels': {'progress': 0.95, 'plan': 0.1}}
    assert not await archive.publication_allowed(await archive.row(old['id']), progress)
    assert await archive.publication_allowed(await archive.row(new['id']), progress)
    assert not await archive.publication_allowed(await archive.row(historical['id']), progress)
    assert not await archive.publication_allowed(await archive.row(new['id']), {'labels': {'progress': 0.95, 'plan': 0.9}})
    assert not await archive.publication_allowed(await archive.row(new['id']), {'labels': {'progress': 0.5}})
    await archive.set_visibility('T1:A', new['id'], 'private')
    assert not await archive.publication_allowed(await archive.row(new['id']), progress)
    await archive.set_sharing_preferences('T1:A', ['progress', 'plan'], False)
    assert not await archive.publication_allowed(await archive.row(new['id']), progress)


@pytest.mark.asyncio
async def test_token_revocation_and_delete_tombstones_prevent_reimport(archive):
    token = await archive.create_token("T1:A")
    assert await archive.token_owner(token["token"]) == "T1:A"
    await archive.revoke_token("T1:B", token["id"])
    assert await archive.token_owner(token["token"]) == "T1:A"
    await archive.revoke_token("T1:A", token["id"])
    assert await archive.token_owner(token["token"]) is None
    body = WorkInput(source_id="deleted", content="Sensitive text")
    saved = await archive.capture("T1:A", body)
    await archive.delete("T1:A", saved["id"])
    assert saved["id"] not in archive.museum.entities
    with pytest.raises(LookupError):
        await archive.read(Reader("T1:A"), saved["id"])
    with pytest.raises(ValueError):
        await archive.capture("T1:A", body)


@pytest.mark.asyncio
async def test_old_processing_lease_cannot_overwrite_requeued_work(archive):
    saved = await archive.capture('T1:A', WorkInput(source_id='lease', content='Original source'))
    old = await archive.next_work()
    await archive.add_rule('T1:A', 'Customer information')
    fresh = await archive.next_work()
    old_payload = await archive.decode(old)
    old_payload['summary'] = 'Outdated model result'
    await archive.complete_work(old, old_payload, private_match=False)
    await archive.fail_work(old, 'indexing')
    current = await archive.row(saved['id'])
    assert current['status'] == 'processing' and current['work_lease'] == fresh['work_lease']
    assert not (await archive.decode(current))['summary']
    fresh_payload = await archive.decode(fresh)
    fresh_payload['summary'] = 'Fresh result'
    await archive.complete_work(fresh, fresh_payload, private_match=False)
    assert (await archive.read(Reader('T1:A'), saved['id']))['summary'] == 'Fresh result'


@pytest.mark.asyncio
async def test_semantic_search_does_not_compare_different_models_with_equal_dimensions(archive):
    saved = await archive.capture('T1:A', WorkInput(source_id='vectors', content='No matching terms'))
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(embedding=[1.0, 0.0], embedding_model='model-a')
    await archive.complete_work(row, payload, private_match=False)
    result = await archive.search(Reader('T1:A'), 'unrelated', vector=[1.0, 0.0], embedding_model='model-b')
    assert result['results'] == [] and result['mode'] == 'keywords'
    result = await archive.search(Reader('T1:A'), 'unrelated', vector=[1.0, 0.0], embedding_model='model-a')
    assert result['results'][0]['id'] == saved['id'] and result['mode'] == 'hybrid'


@pytest.mark.asyncio
async def test_jev_cache_keeps_successful_chunks_and_only_classifies_new_criteria(archive):
    from moegent.gateway import JevResult, NoulAnswer
    from moegent.processing import classify_record

    saved = await archive.capture('T1:A', WorkInput(source_id='chunks', content='a'*15000))
    row = await archive.row(saved['id'])
    payload = await archive.decode(row)
    calls = []
    failed_once = False

    class Gateway:
        async def classify(self, part, questions):
            nonlocal failed_once
            calls.append((part, set(questions)))
            if len(calls) == 2 and not failed_once:
                failed_once = True
                raise RuntimeError('Temporary failure')
            return JevResult(model='jev-pinned', answers={k: NoulAnswer(type='noul', noul=0.9) for k in questions})

    criteria = {'decision': {'instruction': 'Is this a decision?', 'version': 'v1'}}
    with pytest.raises(RuntimeError):
        await classify_record(archive, Gateway(), row, payload, criteria)
    first = len(calls)
    await classify_record(archive, Gateway(), row, payload, criteria)
    assert len(calls) == first + 1
    await classify_record(archive, Gateway(), row, payload, criteria)
    assert len(calls) == first + 1
    criteria['private'] = {'instruction': 'Is this private?', 'version': 'v1'}
    await classify_record(archive, Gateway(), row, payload, criteria)
    assert all(questions == {'private'} for _, questions in calls[first + 1:])


@pytest.mark.asyncio
async def test_publication_never_exposes_original_text_metadata_or_private_embedding(archive):
    saved = await archive.capture('T1:A', WorkInput(source_id='publication', content='PRIVATE_ORIGINAL_ONLY',
                                                  metadata={'detail': 'PRIVATE_METADATA_ONLY'}))
    await archive.set_visibility('T1:A', saved['id'], 'public')
    row = await archive.next_work()
    payload = await archive.decode(row)
    payload.update(summary='Approved work update', embedding=[1.0, 2.0], publication_embedding=[3.0, 4.0],
                   embedding_model='embed-a', labels={'progress': 0.95})
    await archive.complete_work(row, payload, private_match=False)
    public = await archive.read(Reader('T1:B'), saved['id'])
    assert public['content'] == 'Approved work update' and public['embedding'] == [3.0, 4.0]
    assert 'PRIVATE_' not in json.dumps(public) and 'original' not in public
    own = await archive.read(Reader('T1:A'), saved['id'])
    assert own['content'] == 'PRIVATE_ORIGINAL_ONLY' and own['metadata']['detail'] == 'PRIVATE_METADATA_ONLY'
    assert not (await archive.search(Reader('T1:B'), 'PRIVATE_ORIGINAL_ONLY'))['results']
    assert (await archive.search(Reader('T1:B'), 'Approved'))['results']
    assert saved['id'] + ':public' in archive.museum.entities
    await archive.set_visibility('T1:A', saved['id'], 'private')
    with pytest.raises(LookupError):
        await archive.read(Reader('T1:B'), saved['id'])


@pytest.mark.asyncio
async def test_remote_delete_failure_denies_reads_immediately_and_cleanup_resumes(archive):
    saved = await archive.capture('T1:A', WorkInput(source_id='delete-retry', content='Delete me'))
    original_delete = archive.museum.delete
    async def unavailable(record_id):
        raise RuntimeError('Storage is temporarily unavailable')
    archive.museum.delete = unavailable
    await archive.delete('T1:A', saved['id'])
    assert (await archive.row(saved['id']))['status'] == 'deleting'
    with pytest.raises(LookupError):
        await archive.read(Reader('T1:A'), saved['id'])
    assert not (await archive.search(Reader('T1:A'), 'Delete'))['results']
    archive.museum.delete = original_delete
    await archive.db.execute('UPDATE records SET next_attempt=0 WHERE id=?', (saved['id'],))
    await archive.db.commit()
    await archive.cleanup()
    assert (await archive.row(saved['id']))['payload'] == '{}'
    assert saved['id'] not in archive.museum.entities


@pytest.mark.asyncio
async def test_remote_resume_discards_delayed_host_capture_but_allows_explicit_history(archive, monkeypatch):
    from datetime import UTC, datetime
    stamp = 1_000_000_000
    monkeypatch.setattr('moegent.archive.time.time', lambda: stamp)
    def record(identity, seconds):
        return WorkInput(source_id=identity, source='codex', content='Visible work: ' + identity,
                         occurred_at=datetime.fromtimestamp(seconds, UTC) if seconds else None)
    original = await archive.capture('T1:A', record('before', stamp - 30))
    await archive.preferences('T1:A', capture_enabled=False)
    with pytest.raises(PermissionError):
        await archive.capture('T1:A', record('paused', stamp - 10))
    await archive.preferences('T1:A', capture_enabled=True)
    writes_before = archive.museum.writes
    for body in [record('paused', stamp - 10), record('unknown', None)]:
        assert await archive.capture('T1:A', body) == {'status': 'skipped', 'reason': 'outside_capture_window'}
    assert archive.museum.writes == writes_before
    again = await archive.capture('T1:A', record('before', stamp - 30))
    assert again['id'] == original['id'] and again['duplicate']
    await archive.capture('T1:A', record('after', stamp + 1))
    imported = await archive.capture('T1:A', record('paused', stamp - 10).model_copy(update={'historical': True}))
    assert (await archive.read(Reader('T1:A'), imported['id']))['historical']
    monkeypatch.setattr('moegent.archive.time.time', lambda: stamp + 60)
    await archive.preferences('T1:A', capture_enabled=True)
    assert (await archive.member('T1:A'))['capture_after'] == stamp  # Saving an unchanged preference is not a new resume.
