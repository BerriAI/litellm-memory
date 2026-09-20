import json

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from moegent.config import Settings
from moegent.observability import configure


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
