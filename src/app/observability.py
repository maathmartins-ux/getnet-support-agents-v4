"""Logs JSON com trace_id, spans por etapa, métricas Prometheus e exportador OpenTelemetry opcional.

- Sempre: cada `span()` vira uma linha JSON no log (com o trace_id do request) e uma latência em /metrics.
- Com OTEL_EXPORTER=console|otlp e o extra [otel] instalado: o mesmo `span()` também abre um span
  OpenTelemetry (atributo `app.trace_id` liga o span ao log). Sem o extra, segue só com o log —
  nunca quebra a aplicação. O exportador fica fora da imagem padrão (requirements.lock).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

from .config import PRICES_PER_MTOK, otel_exporter

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_usage: ContextVar[dict | None] = ContextVar("usage", default=None)
_usage_lock = threading.Lock()  # agentes em paralelo somam no mesmo dict do request


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": round(record.created, 3), "level": record.levelname,
                   "msg": record.getMessage(), "trace_id": _trace_id.get()}
        payload.update(getattr(record, "extra_fields", {}))
        return json.dumps(payload, ensure_ascii=False)


log = logging.getLogger("app")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(JsonFormatter())
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False
# httpx loga a URL completa em INFO; a do Telegram carrega o token do bot.
logging.getLogger("httpx").setLevel(logging.WARNING)


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:16]
    _trace_id.set(tid)
    _usage.set({"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    return tid


def current_trace_id() -> str:
    return _trace_id.get()


def _esc(value: object) -> str:
    # Formato de exposição Prometheus: rótulo com aspas, barra ou quebra de linha forjaria séries.
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[tuple, float] = defaultdict(float)
        self.latency: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def inc(self, name: str, **labels: str) -> None:
        self.add(name, 1, **labels)

    def add(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self.counters[(name, tuple(sorted(labels.items())))] += value

    def get(self, name: str, **labels: str) -> float:
        return self.counters.get((name, tuple(sorted(labels.items()))), 0.0)

    def observe(self, span: str, ms: float) -> None:
        with self._lock:
            self.latency[span].append(ms)

    def render(self) -> str:
        lines = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels)
                lines.append(f"{name}{{{lab}}} {value:g}")
            for span, values in sorted(self.latency.items()):
                lines.append(f'span_latency_ms_sum{{span="{span}"}} {sum(values):.3f}')
                lines.append(f'span_latency_ms_count{{span="{span}"}} {len(values)}')
        return "\n".join(lines) + "\n"


METRICS = Metrics()

# --- OpenTelemetry opcional ---------------------------------------------------------------------
_otel = {"tracer": None, "provider": None, "exporter": None}


def configure_otel(exporter: str | None = None, stream=None) -> bool:
    """Liga o exportador OTel. `exporter` padrão = OTEL_EXPORTER. Devolve False se desligado ou sem o extra.

    `stream` só vale para o console (testes capturam a saída). O provider global só é registrado uma
    vez (regra do OTel); o tracer do app usa o provider próprio, então reconfigurar em teste funciona.
    """
    name = exporter or otel_exporter()
    if not name:
        _otel.update(tracer=None, provider=None, exporter=None)
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
    except ImportError:
        log.warning("otel_unavailable", extra={"extra_fields": {"reason": "instale o extra [otel]"}})
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": "app"}))
    if name == "console":
        exp = ConsoleSpanExporter(out=stream) if stream is not None else ConsoleSpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exp))
    else:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            log.warning("otel_unavailable", extra={"extra_fields": {"reason": "sem exportador OTLP"}})
            return False
        exp = OTLPSpanExporter()  # endpoint por OTEL_EXPORTER_OTLP_ENDPOINT (padrão do OTel)
        provider.add_span_processor(BatchSpanProcessor(exp))
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(provider)  # SDK da Anthropic aninha os spans dele sob os nossos
    _otel.update(tracer=provider.get_tracer("app"), provider=provider, exporter=name)
    return True


def otel_enabled() -> str | None:
    return _otel["exporter"]


@contextmanager
def span(name: str, **fields):
    start = time.perf_counter()
    tracer = _otel["tracer"]
    cm = tracer.start_as_current_span(name) if tracer else nullcontext()
    with cm as otel_span:
        if otel_span is not None:
            otel_span.set_attribute("app.trace_id", _trace_id.get())
            for k, v in fields.items():
                otel_span.set_attribute(f"app.{k}", str(v))
        try:
            yield fields  # o chamador pode anexar o desfecho (ex.: status do agente) antes do log
        finally:
            ms = (time.perf_counter() - start) * 1000
            METRICS.observe(name, ms)
            if otel_span is not None:
                for k, v in fields.items():
                    otel_span.set_attribute(f"app.{k}", str(v))
            log.info("span", extra={"extra_fields": {"span": name, "ms": round(ms, 2), **fields}})


def record_llm_usage(model: str, input_tokens: int, output_tokens: int) -> float:
    """Contabiliza tokens e custo da chamada no request atual, nas métricas e no log."""
    price = PRICES_PER_MTOK.get(model)
    cost = (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000 if price else 0.0
    METRICS.add("llm_tokens_total", input_tokens, model=model, kind="input")
    METRICS.add("llm_tokens_total", output_tokens, model=model, kind="output")
    METRICS.add("llm_cost_usd_total", cost, model=model)
    if price is None:
        METRICS.inc("llm_unpriced_calls_total", model=model)
    usage = _usage.get()
    if usage is not None:
        with _usage_lock:
            usage["input_tokens"] += input_tokens
            usage["output_tokens"] += output_tokens
            usage["cost_usd"] = round(usage["cost_usd"] + cost, 6)
    log.info("llm_usage", extra={"extra_fields": {"model": model, "input_tokens": input_tokens,
                                                  "output_tokens": output_tokens, "cost_usd": round(cost, 6)}})
    return cost


def request_usage() -> dict:
    return dict(_usage.get() or {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
