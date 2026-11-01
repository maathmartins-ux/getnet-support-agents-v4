"""Item 10 — exportador OpenTelemetry opcional; o log JSON com trace_id continua igual."""
import io
import json
import logging

import pytest

pytest.importorskip("opentelemetry.sdk")  # está na lock de dev; fora da imagem padrão

from app.observability import configure_otel, log, new_trace_id, otel_enabled, span  # noqa: E402


def test_disabled_by_default():
    assert configure_otel() is False and otel_enabled() is None


def test_console_exporter_emits_span_linked_to_trace_id():
    out = io.StringIO()
    assert configure_otel("console", stream=out) is True and otel_enabled() == "console"
    tid = new_trace_id()
    with span("agent", agent="faq"):
        pass
    exported = json.loads(out.getvalue())
    assert exported["name"] == "agent"
    assert exported["attributes"]["app.trace_id"] == tid and exported["attributes"]["app.agent"] == "faq"


def test_json_log_keeps_trace_id_with_otel_on():
    configure_otel("console", stream=io.StringIO())
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(log.handlers[0].formatter)
    log.addHandler(handler)
    try:
        tid = new_trace_id()
        with span("router"):
            pass
    finally:
        log.removeHandler(handler)
    line = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert line["trace_id"] == tid and line["span"] == "router"
