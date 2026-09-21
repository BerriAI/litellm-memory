import json

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from litellm_memory.config import Settings
from litellm_memory.observability import configure


def test_observability_is_off_by_default_and_enabled_spans_never_record_exception_text(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path)
    assert configure(settings).client is None
    exporter = InMemorySpanExporter()
    telemetry = configure(settings, span_processors=[SimpleSpanProcessor(exporter)])
    with telemetry.operation('classification'):
        pass
    with pytest.raises(ValueError):
        with telemetry.operation('query'):
            raise ValueError('PRIVATE_PROMPT_CANARY credential=SECRET_CANARY')
    telemetry.client.force_flush()
    spans = exporter.get_finished_spans()
    assert spans
    data = json.dumps([span.to_json() for span in spans])
    assert 'PRIVATE_PROMPT_CANARY' not in data and 'SECRET_CANARY' not in data
    assert 'exception.message' not in data and 'exception.stacktrace' not in data
    assert any(span.attributes.get('outcome') == 'error' for span in spans)
    assert any(span.attributes.get('outcome') == 'ok' for span in spans)


@pytest.mark.asyncio
async def test_query_and_tool_spans_keep_usage_but_not_tool_arguments(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path)
    exporter = InMemorySpanExporter()
    telemetry = configure(settings, span_processors=[SimpleSpanProcessor(exporter)])

    @telemetry.tool('search_work')
    async def search_work(query):
        return 'PRIVATE_RESULT_CANARY ' + query

    with telemetry.operation('query', source='mcp', audience='owner', model='gateway-route') as run:
        assert 'PRIVATE_RESULT' in await search_work('PRIVATE_QUERY_CANARY')
        run.set(input_tokens=32, output_tokens=16, model_requests=2, source_count=1)
    telemetry.client.force_flush()
    spans = exporter.get_finished_spans()
    data = json.dumps([span.to_json() for span in spans])
    assert 'PRIVATE_QUERY_CANARY' not in data and 'PRIVATE_RESULT_CANARY' not in data
    query = next(s for s in spans if s.name == 'litellm-memory.query')
    tool = next(s for s in spans if s.name == 'litellm-memory.tool')
    assert tool.parent.span_id == query.context.span_id
    assert query.attributes['source'] == 'mcp'
    assert query.attributes['input_tokens'] == 32
    assert tool.attributes['tool'] == 'search_work'


def test_payload_and_identity_attributes_are_rejected_even_when_tracing_is_off():
    from litellm_memory.observability import Telemetry
    for attribute in ['prompt', 'answer', 'email', 'member_id', 'token', 'url']:
        with pytest.raises(ValueError, match='Unsupported telemetry attribute'):
            with Telemetry().operation('query', **{attribute: 'PRIVATE_CANARY'}):
                pass
