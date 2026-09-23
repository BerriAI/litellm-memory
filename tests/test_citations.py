import pytest

from litellm_memory.query import CitationError, validate_citations


def test_citations_must_resolve_to_evidence_read_in_this_authorized_run():
    base = 'https://memory.example'
    sources = {'a' * 64, 'b' * 64}
    good = f'The work is underway. [Evidence]({base}/memory/{"a" * 64}).'
    assert validate_citations(good, sources, base) == good
    assert validate_citations('No matching work was found.', set(), base)
    for target in [base + '/memory/' + 'c' * 64,
                   'https://attacker.example/memory/' + 'a' * 64,
                   base + '/memory/' + 'a' * 64 + '?content=private',
                   base + '/memory/../settings', '/memory/' + 'a' * 64]:
        with pytest.raises(CitationError):
            validate_citations(f'A claim. [Source]({target})', sources, base)
    with pytest.raises(CitationError):
        validate_citations('A claim without evidence links.', sources, base)


def test_slack_native_source_links_use_the_same_verification_as_markdown():
    base = 'https://memory.example'
    record_id = 'a' * 64
    native = f'Work is in progress. <{base}/memory/{record_id}|Work source>'
    assert validate_citations(native, {record_id}, base) == f'Work is in progress. [Work source]({base}/memory/{record_id})'
    for target in ['https://other.example/memory/' + record_id,
                   base + '/memory/unknown', base + '/memory/' + record_id + '?secret=text']:
        with pytest.raises(CitationError):
            validate_citations(f'Claim <{target}|Source>', {record_id}, base)


@pytest.mark.asyncio
async def test_missing_citations_repair_once_using_authorized_evidence():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from litellm_memory.archive import Reader
    from litellm_memory.query import verified_answer

    settings = SimpleNamespace(public_base_url='https://memory.example')
    archive = SimpleNamespace(read=AsyncMock(return_value={'title': 'Build', 'summary': 'Built a widget', 'content': 'Private original'}))
    gateway = SimpleNamespace(generate=AsyncMock(return_value='Built a widget. [Build](memory-source:S1)'))
    result = await verified_answer('Built a widget.', {'known'}, settings, archive, gateway, Reader('T:A'), 'chosen-agent')
    assert '[Build]' in result
    assert result.endswith('(https://memory.example/memory/known)')
    assert 'memory-source:' not in result
    assert gateway.generate.call_args.kwargs['model'] == 'chosen-agent'
    assert 'Private original' not in str(gateway.generate.call_args)
    archive.read.assert_awaited_once_with(Reader('T:A'), 'known')
    gateway.generate.return_value = 'Invented citation [Source](https://memory.example/memory/unknown)'
    with pytest.raises(CitationError):
        await verified_answer('Uncited.', {'known'}, settings, archive, gateway, Reader('T:A'), 'chosen-agent')
    assert gateway.generate.await_count == 2  # One attempt per failed answer, no retry loop.
    archive.read.side_effect = PermissionError('Source was withdrawn')
    with pytest.raises(PermissionError):
        await verified_answer('Uncited.', {'known'}, settings, archive, gateway, Reader('T:A'), 'chosen-agent')
    assert gateway.generate.await_count == 2


@pytest.mark.asyncio
async def test_grounding_maps_distinct_source_references_and_rejects_unknown_ones():
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from litellm_memory.archive import Reader
    from litellm_memory.query import verified_answer

    async def read(_reader, record_id):
        return {'title': record_id, 'summary': 'Savings' if record_id == 'a' else 'Pagination'}

    settings = SimpleNamespace(public_base_url='https://memory.example')
    archive = SimpleNamespace(read=read)
    gateway = SimpleNamespace(generate=AsyncMock(return_value='Pagination. [Rows](memory-source:S2)'))
    draft = 'Pagination. [Wrong source](https://memory.example/memory/a)'
    result = await verified_answer(draft, {'b', 'a'}, settings, archive, gateway, Reader('T:A'), 'model', ground=True)
    assert result == 'Pagination. [Rows](https://memory.example/memory/b)'
    context = json.loads(gateway.generate.call_args.args[0][-1]['content'])
    assert context['evidence'][1]['source'] == 'S2'
    assert context['evidence'][1]['text'] == 'Pagination'
    assert '/memory/' not in json.dumps(context)
    gateway.generate.return_value = 'Pagination. [Rows](memory-source:S3)'
    with pytest.raises(CitationError, match='unknown evidence reference'):
        await verified_answer(draft, {'b', 'a'}, settings, archive, gateway, Reader('T:A'), 'model', ground=True)


@pytest.mark.asyncio
async def test_citation_repair_never_turns_empty_evidence_into_a_successful_answer():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from litellm_memory.archive import Reader
    from litellm_memory.query import verified_answer

    gateway = SimpleNamespace(generate=AsyncMock(return_value='No evidence available.'))
    with pytest.raises(CitationError):
        await verified_answer('[Source](https://memory.example/memory/invented)', set(),
            SimpleNamespace(public_base_url='https://memory.example'), SimpleNamespace(), gateway, Reader('T:A'), 'model')
    gateway.generate.assert_not_called()
