"""Opt-in Logfire/OTLP operational spans, without prompts or exception payloads."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

request_source = ContextVar('litellm_memory_request_source', default='api')
TOOLS = {'search_work', 'read_work', 'list_people', 'work_overview', 'update_status'}


class Operation:
    """An explicit allowlist prevents accidental payload or identity export."""
    def __init__(self, span):
        self.span = span

    def set(self, **attributes):
        for key, value in attributes.items():
            valid = (
                key == 'source' and value in {'web', 'slack', 'mcp', 'api', 'background'}
                or key == 'audience' and value in {'owner', 'public'}
                or key == 'tool' and value in TOOLS
                or key == 'model' and isinstance(value, str) and 0 < len(value) <= 200
                or key in {'input_tokens', 'output_tokens', 'model_requests', 'source_count',
                           'item_count', 'criterion_count', 'status_code'}
                and isinstance(value, int) and not isinstance(value, bool) and value >= 0
            )
            if not valid:
                raise ValueError('Unsupported telemetry attribute')
            if self.span:
                self.span.set_attribute(key, value)



class Telemetry:
    def __init__(self, client=None):
        self.client = client

    @contextmanager
    def operation(self, operation, **attributes):
        if operation not in {'request', 'classification', 'generation', 'embedding', 'query', 'tool', 'setup_check'}:
            raise ValueError('Unknown telemetry operation')
        span = self.client.span('litellm-memory.' + operation) if self.client else None
        if span:
            span.__enter__()
        try:
            record = Operation(span)
            record.set(**attributes)
            yield record
        except asyncio.CancelledError:
            if span:
                span.set_attribute('outcome', 'cancelled')
            raise
        except BaseException:
            if span:
                span.set_attribute('outcome', 'error')
            raise
        else:
            if span:
                span.set_attribute('outcome', 'ok')
        finally:
            if span:
                # Exception text/frames may contain a prompt or source. End the
                # span without passing an exception to Logfire's auto-recorder.
                span.__exit__(None, None, None)

    def tool(self, name):
        if name not in TOOLS:
            raise ValueError('Unsupported telemetry tool')
        def decorate(function):
            @wraps(function)
            async def wrapped(*args, **kwargs):
                with self.operation('tool', tool=name):
                    return await function(*args, **kwargs)
            return wrapped
        return decorate


def configure(settings, *, span_processors=None):
    if settings.observability_export == 'none' and span_processors is None:
        return Telemetry()
    import logfire
    processors = list(span_processors or [])
    if settings.observability_export == 'otlp':
        if not settings.otlp_traces_endpoint:
            raise ValueError('Set OTLP_TRACES_ENDPOINT for the selected telemetry exporter.')
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        processors.append(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_traces_endpoint)))
    if settings.observability_export == 'logfire' and not settings.logfire_token:
        raise ValueError('Set LOGFIRE_TOKEN for the selected telemetry exporter.')
    client = logfire.configure(local=True, service_name='litellm-memory', service_version='0.2.0',
        send_to_logfire=settings.observability_export == 'logfire', token=settings.logfire_token or None,
        console=False, metrics=False, inspect_arguments=False, add_baggage_to_attributes=False,
        distributed_tracing=False, additional_span_processors=processors,
        config_dir=settings.state_dir / 'observability', data_dir=settings.state_dir / 'observability',
        advanced=logfire.AdvancedOptions(resource_detectors=[], emit_configuration_span=False))
    return Telemetry(client)


def main():
    """Emit one content-free trace to verify the operator's exporter settings."""
    from .config import Settings
    telemetry = configure(Settings())
    if telemetry.client is None:
        raise SystemExit('Tracing is off. Set OBSERVABILITY_EXPORT to logfire or otlp first.')
    with telemetry.operation('setup_check'):
        pass
    telemetry.client.force_flush()
    print('Trace export attempted. Look for litellm-memory.setup_check in your backend to confirm receipt.')


if __name__ == '__main__':
    main()
