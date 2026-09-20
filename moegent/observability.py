"""Opt-in Logfire/OTLP operational spans, without prompts or exception payloads."""

from contextlib import contextmanager


class Telemetry:
    def __init__(self, client=None):
        self.client = client

    @contextmanager
    def operation(self, operation):
        if operation not in {'request', 'classification', 'generation', 'embedding', 'query'}:
            raise ValueError('Unknown telemetry operation')
        span = self.client.span('litemem.' + operation) if self.client else None
        if span:
            span.__enter__()
        try:
            yield
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
    client = logfire.configure(local=True, service_name='litemem', service_version='0.1.0',
        send_to_logfire=settings.observability_export == 'logfire', token=settings.logfire_token or None,
        console=False, metrics=False, inspect_arguments=False, add_baggage_to_attributes=False,
        distributed_tracing=False, additional_span_processors=processors,
        config_dir=settings.state_dir / 'observability', data_dir=settings.state_dir / 'observability',
        advanced=logfire.AdvancedOptions(resource_detectors=[], emit_configuration_span=False))
    return Telemetry(client)
