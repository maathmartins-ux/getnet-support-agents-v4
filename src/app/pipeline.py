"""Orquestração de um turno: guardrails → memória → router → supervisor → saída segura → persistência.

Os forks trocam agentes e regras do router; a assinatura de `handle` (ChatRequest, user_id
autenticado, identity_verified -> ChatResponse) não muda.

Latência: com LLM, o classificador de segurança (camada 2) e o router rodam em paralelo sobre o texto
já mascarado; se o guardrail bloquear, a decisão do router é descartada.
"""
from __future__ import annotations

import contextvars
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import supervisor
from .agents import Context
from .grounding import risk_flags, unsupported_numbers
from .guardrails import Verdict, llm_check, mask_output, pre_check
from .llm import get_llm
from .observability import METRICS, log, new_trace_id, request_usage, span
from .rag import get_index
from .router import detect_language, route
from .schemas import ChatRequest, ChatResponse, Handoff, RouterDecision, Usage
from .store import get_store

REFUSAL = {
    "pt": "Não posso atender a esse pedido. Posso ajudar com dúvidas sobre o produto ou sua conta.",
    "en": "I can't help with that request. I can help with product or account questions.",
}
NO_ANSWER = {
    "pt": "Não encontrei base para responder com segurança. Posso encaminhar para um atendente humano.",
    "en": "I couldn't find a reliable basis to answer. I can hand this to a human agent.",
}
SUMMARY_TURNS = 5
HISTORY_WINDOW = 20  # mensagens recentes inteiras no contexto (10 turnos)
DIGEST_ITEMS = 8  # falas antigas resumidas além da janela
# O roteador recebe a conversa INTEIRA: o assunto do turno 1 decide "e ele já saiu?" no turno 14 (v3: com só
# 20 mensagens, o 1º turno de uma conversa de 26 sumiu). Fora da janela, só falas do cliente, cortadas e com teto.
ROUTER_OLDER_CHARS = 120
ROUTER_OLDER_MAX = 200  # 200 x 120 caracteres: teto de memória; o prompt do LLM usa só intenções e as 4 últimas


def _router_history(store, conversation_id: str, history: list[dict]) -> list[dict]:
    older = [{"role": "user", "content": m["content"][:ROUTER_OLDER_CHARS], "intent": m["intent"]}
             for m in store.older_messages(conversation_id, skip_last=HISTORY_WINDOW) if m["role"] == "user"]
    return older[-ROUTER_OLDER_MAX:] + history


def _update_summary(store, user_id: str, intent: str, text: str) -> None:
    """Memória de longo prazo determinística: últimos assuntos do usuário, entre conversas.

    Determinística de propósito: auditável, sem custo e sem PII (o texto já chega mascarado).
    """
    items = [i for i in store.user_summary(user_id).split(" | ") if i]
    items.append(f"{intent}: {text[:80]}")
    store.set_user_summary(user_id, " | ".join(items[-SUMMARY_TURNS:]))


def _digest(store, conversation_id: str) -> str:
    """Resumo rolante e determinístico do que saiu da janela recente (o turno 15 ainda sabe do turno 1).

    Guarda as falas do cliente e do atendente (com a intenção), já mascaradas; sem LLM, sem custo, auditável.
    """
    older = store.older_messages(conversation_id, skip_last=HISTORY_WINDOW)
    items = [f"[{m['intent'] or m['role']}] {m['content'][:120]}" for m in older if m["role"] != "assistant"]
    return " | ".join(items[-DIGEST_ITEMS:])


def _guard_and_route(message: str, history: list[dict]) -> tuple[Verdict, RouterDecision | None]:
    with span("guardrails"):
        verdict = pre_check(message)
    if verdict.blocked:
        return verdict, None
    if not get_llm("guardrail").enabled:
        with span("router"):
            return verdict, route(verdict.text, history)
    with ThreadPoolExecutor(max_workers=2) as pool:
        g = pool.submit(contextvars.copy_context().run, llm_check, verdict)
        r = pool.submit(contextvars.copy_context().run, route, verdict.text, history)
        with span("router"):
            decision = r.result()
        verdict = g.result()
    return verdict, (None if verdict.blocked else decision)


def handle(req: ChatRequest, user_id: str, identity_verified: bool = True) -> ChatResponse:
    t0 = time.perf_counter()
    resp = _handle(req, user_id, identity_verified)
    try:  # analytics é melhor esforço: falha ao registrar nunca derruba o atendimento
        get_store().log_request({
            "trace_id": resp.trace_id, "conversation_id": resp.conversation_id, "user_id": user_id,
            "intent": resp.intent, "route": resp.route, "handoff": resp.handoff.required, "blocked": resp.blocked,
            "ungrounded": bool(resp.unsupported_numbers), "cost_usd": resp.usage.cost_usd,
            "ms": round((time.perf_counter() - t0) * 1000, 1)})  # sem o texto: analytics não guarda conteúdo
    except Exception as exc:  # noqa: BLE001
        log.warning("analytics_log_failed", extra={"extra_fields": {"error": type(exc).__name__}})
    return resp


def _handle(req: ChatRequest, user_id: str, identity_verified: bool = True) -> ChatResponse:
    trace_id = new_trace_id()
    store = get_store()
    conversation_id = req.conversation_id or uuid.uuid4().hex[:12]
    owner = store.conversation_owner(conversation_id)
    if owner and owner != user_id:
        raise PermissionError("conversation belongs to another user")
    language = detect_language(req.message)
    base = {"trace_id": trace_id, "conversation_id": conversation_id, "user_id": user_id,
            "identity_verified": identity_verified}
    if not identity_verified:
        log.info("unverified_identity", extra={"extra_fields": {"user_id": user_id, "auth_mode": "demo"}})

    history = store.history(conversation_id, limit=HISTORY_WINDOW)
    verdict, decision = _guard_and_route(req.message, _router_history(store, conversation_id, history))
    if verdict.blocked or decision is None:
        METRICS.inc("chat_requests_total", intent="blocked")
        METRICS.inc("guardrail_blocks_total", reason=verdict.reason or "unknown")
        return ChatResponse(**base, answer=REFUSAL[language], intent="blocked", route=["guardrails"],
                            language=language, blocked=True, guardrails=verdict.flags, usage=Usage(**request_usage()))

    language = decision.language
    ctx = Context(text=verdict.text, language=language, user_id=user_id, conversation_id=conversation_id,
                  intent=decision.intent, history=history, user_summary=store.user_summary(user_id),
                  conversation_digest=_digest(store, conversation_id))
    outcome = supervisor.run(list(decision.plan), ctx, decision.confidence)

    answer = outcome.answer or "\n\n".join(r.text for r in outcome.results if r.text)
    if not answer.strip():  # nunca 200 vazio: vazio no histórico vira 400 da API no turno seguinte
        answer = NO_ANSWER[language]
    docs = {d.id: d.text for d in get_index().docs}
    public = "\n".join(docs.get(s.id, "") for r in outcome.results for s in r.sources)
    answer = mask_output(answer, public=public)
    # número que não está em fonte, tool, template, pergunta nem fala anterior do cliente/operador → sinalizado
    said = " ".join([verdict.text, ctx.conversation_digest,
                     *(m["content"] for m in history if m.get("role") != "assistant")])
    unsupported = unsupported_numbers(answer, [e for r in outcome.results for e in r.evidence], question=said)
    if unsupported:
        METRICS.inc("numeric_ungrounded_total")
        log.warning("numeric_ungrounded", extra={"extra_fields": {"trace_id": trace_id, "numbers": unsupported}})
    risks = risk_flags(answer, [e for r in outcome.results for e in r.evidence])
    for label in risks:
        METRICS.inc("risk_flags_total", flag=label)
    if risks:
        log.warning("risk_flagged", extra={"extra_fields": {"trace_id": trace_id, "flags": risks}})
    handoff = next((r.handoff for r in outcome.results if r.handoff), None) or Handoff()
    store.add_message(conversation_id, user_id, "user", verdict.text, decision.intent)  # texto já mascarado
    store.add_message(conversation_id, user_id, "assistant", answer, decision.intent)
    _update_summary(store, user_id, decision.intent, verdict.text)

    METRICS.inc("chat_requests_total", intent=decision.intent)
    if handoff.required:
        METRICS.inc("handoffs_total", reason=handoff.reason or "unknown")
    return ChatResponse(
        **base, answer=answer, intent=decision.intent, confidence=decision.confidence,
        route=["router", *outcome.route], replans=outcome.replans, engine=outcome.engine, language=language,
        sources=[s for r in outcome.results for s in r.sources],
        tool_calls=[c for r in outcome.results for c in r.tool_calls], handoff=handoff,
        pending_action=next((r.pending_action for r in outcome.results if r.pending_action), None),
        guardrails=verdict.flags, unsupported_numbers=unsupported, risk_flags=risks, usage=Usage(**request_usage()),
        # mesma evidência da checagem de números, mascarada como a resposta: o eval grava e o juiz lê
        evidence=[mask_output(e, public=public) for r in outcome.results for e in r.evidence])
