"""Dois provedores atrás da mesma interface: offline (determinístico, CI) e Anthropic.

Quem usa LLM chama `get_llm(<papel>)` (router, guardrail, supervisor, agent, judge), checa `.enabled`
e trata `LLMError` caindo no caminho offline; ninguém importa o SDK. Modelo e effort vêm do papel
(config.ROLES, trocáveis por env). Toda chamada real registra tokens e custo.

Regras da API (skill claude-api, 30/10/2026): sem temperature/top_p/top_k (400 desde 21/10/2026);
effort explícito em output_config.effort; structured output em output_config.format; sem tool_choice
forçado (o padrão é auto; any/tool dão 400 no Sonnet 5.5 e no Opus 5.5); stop_reason "refusal"
checado antes do conteúdo; erros do SDK capturados do mais específico ao mais geral.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .config import ROLES, effort_for, llm_api_key, llm_base_url, llm_provider, model_for
from .observability import METRICS, log, record_llm_usage, span


class LLMError(RuntimeError):
    """Falha do provedor já classificada; o chamador cai no caminho offline."""

    def __init__(self, message: str, kind: str = "other") -> None:
        super().__init__(message)
        self.kind = kind  # rate_limited | auth | api_status | connection | refusal | max_tokens | config | other


@dataclass
class LLMReply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    def json(self) -> dict:
        return json.loads(self.text)


class OfflineLLM:
    enabled = False
    model = "offline"
    effort = None

    def complete(self, system: str, messages: list[dict], max_tokens: int = 4096,
                 schema: dict | None = None) -> LLMReply:
        raise LLMError("modo offline: use o caminho determinístico", "offline")

    def create(self, system: str, messages: list, tools: list[dict], max_tokens: int = 8192):
        raise LLMError("modo offline: use o caminho determinístico", "offline")


class AnthropicLLM:
    enabled = True

    def __init__(self, model: str, effort: str, client=None) -> None:
        import anthropic  # import tardio: o modo offline não depende do SDK

        self._sdk = anthropic
        # 60 s por tentativa e 2 retries do SDK (429/5xx/conexão): pior caso ~3 min por chamada.
        # chave e endereço do APP (config.llm_api_key/llm_base_url): na nuvem, nunca os do próprio Claude Code
        self.client = client or anthropic.Anthropic(api_key=llm_api_key(), base_url=llm_base_url(),
                                                    timeout=60.0, max_retries=2)
        self.model = model
        self.effort = effort

    def _fail(self, kind: str, message: str) -> LLMError:
        METRICS.inc("llm_errors_total", model=self.model, kind=kind)
        return LLMError(message, kind)

    def _call(self, **kwargs):
        sdk = self._sdk
        output_config = {"effort": self.effort, **kwargs.pop("output_config", {})}
        with span("llm", model=self.model, effort=self.effort):
            try:
                resp = self.client.messages.create(model=self.model, output_config=output_config, **kwargs)
            except sdk.RateLimitError as exc:
                raise self._fail("rate_limited", f"rate_limited: {exc}") from exc
            except sdk.APIStatusError as exc:
                kind = "auth" if exc.status_code in (401, 403) else "api_status"
                raise self._fail(kind, f"api_status_{exc.status_code}: {exc.message}") from exc
            except sdk.APIConnectionError as exc:
                raise self._fail("connection", f"connection: {exc}") from exc
            except sdk.APIError as exc:  # ex.: APIResponseValidationError
                raise self._fail("other", f"api_error: {type(exc).__name__}") from exc
            except TypeError as exc:  # SDK sem credencial resolvível levanta TypeError
                raise self._fail("auth", f"client_config: {exc}") from exc
        record_llm_usage(self.model, resp.usage.input_tokens, resp.usage.output_tokens)
        if resp.stop_reason == "refusal":
            raise self._fail("refusal", "refusal")
        return resp

    def complete(self, system: str, messages: list[dict], max_tokens: int = 4096,
                 schema: dict | None = None) -> LLMReply:
        extra = {"output_config": {"format": {"type": "json_schema", "schema": schema}}} if schema else {}
        resp = self._call(max_tokens=max_tokens, system=system, messages=messages, **extra)
        if resp.stop_reason == "max_tokens":
            raise self._fail("max_tokens", "resposta truncada em max_tokens")  # JSON cortado não é dado
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMReply(text, self.model, resp.usage.input_tokens, resp.usage.output_tokens)

    def create(self, system: str, messages: list, tools: list[dict], max_tokens: int = 8192):
        """Uma volta do loop manual de tool use: devolve a resposta bruta (o chamador executa as tools)."""
        return self._call(max_tokens=max_tokens, system=system, messages=messages, tools=tools)


@lru_cache(maxsize=16)
def _anthropic(model: str, effort: str) -> AnthropicLLM:
    return AnthropicLLM(model, effort)  # um cliente (pool HTTP) por modelo/effort, não um por chamada


def get_llm(role: str = "agent") -> OfflineLLM | AnthropicLLM:
    if llm_provider() == "anthropic":
        return _anthropic(model_for(role), effort_for(role))
    return OfflineLLM()


def warmup() -> int:
    """Cria na subida um cliente por (modelo, effort) dos papéis; devolve quantos ficaram prontos.

    `import anthropic` levou ~7 s no Windows do smoke de 31/10/2026 e caía dentro do 1º request de cada
    processo (p50 do split official 22,8 s, ~10 s antes da 1ª chamada). Falha aqui só loga: aquecer é
    otimização, nunca motivo para não subir.
    """
    if llm_provider() != "anthropic":
        return 0
    pairs = {(model_for(role), effort_for(role)) for role in ROLES}
    try:
        for model, effort in pairs:
            _anthropic(model, effort)
    except Exception as exc:  # noqa: BLE001 — qualquer falha de import/config vira aviso, não queda
        log.warning("llm_warmup_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        return 0
    return len(pairs)
