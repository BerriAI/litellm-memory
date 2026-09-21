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
