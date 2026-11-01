"""Domínio do desafio — FLUXO (frente R): taxonomia, planos, FALLBACK, regras do router.

Contrato lido pelo motor: PLANS, FALLBACK, ALLOWED, DEPENDS, NO_ESCALATION, DEFAULT_INTENT,
MULTI_PARTS, states_subject(), clause_intent(), combine().
Teste: tests/test_domain_router.py.
"""
from __future__ import annotations

import re

# --- taxonomia e planos (lidos por router e supervisor) ---------------------------------------------------
# intent → plano inicial. "multi" = duas perguntas independentes no mesmo turno → rodam em paralelo.
PLANS = {
    "faq": ["faq"],
    "records": ["records"],
    "multi": ["faq", "records"],
    "human_request": ["escalation"],
}
# Status != ok → próximo agente. Sem fonte ou ferramenta quebrada termina em humano.
FALLBACK = {"faq": "escalation", "records": "escalation"}
# Transições permitidas (de → para). None = início. Escalation é terminal.
ALLOWED = {None: {"faq", "records", "escalation"}, "faq": {"records", "escalation"},
           "records": {"faq", "escalation"}, "escalation": set()}
# Quem precisa dos fatos de quem (não roda no mesmo lote). Ex. Getnet: {"faq": {"records"}} (troubleshoot).
DEPENDS: dict[str, set[str]] = {}
# Intents que nunca abrem caso humano (ex. Getnet: {"general"}, clima e câmbio).
NO_ESCALATION: set[str] = set()
DEFAULT_INTENT = "faq"  # cláusula com assunto do domínio e sem regra
MULTI_PARTS = {"faq", "records"}  # o que uma frase-exemplo "multi" do k-NN soma às intenções da mensagem

# --- regras do router offline -------------------------------------------------------------------------------
_HUMAN = re.compile(r"\b(atendente|humano|pessoa real|falar com algu[eé]m|human|representative|real person"
                    r"|operador|operator)\b", re.IGNORECASE)
_RECORDS = re.compile(r"\b(pedido|pedidos|order|orders|cancel\w*|\d{4})\b", re.IGNORECASE)
_FOLLOWUP = re.compile(r"\b(ele|ela|isso|esse|essa|it|that|this one)\b", re.IGNORECASE)


def states_subject(text: str) -> bool:
    """A fala define assunto por REGRA (sem olhar vocabulário)? Usado para achar o assunto da conversa."""
    return bool(_HUMAN.search(text) or _RECORDS.search(text))


def clause_intent(text: str, history: list[dict], r) -> str | None:
    """Intenção de UMA cláusula. `r` = módulo app.router (topic_intent, knn_intent, has_subject)."""
    if _HUMAN.search(text):
        return "human_request"
    if _RECORDS.search(text):
        return "records"
    last = r.topic_intent(history)
    if last in ("records", "multi") and _FOLLOWUP.search(text):  # memória: pergunta de seguimento
        return "records"
    # Nenhuma regra casou: o k-NN dos exemplos rotulados generaliza a paráfrase ("my approved sales").
    if (near := r.knn_intent(text)) is not None:
        return near
    # Cláusula sem vocabulário do domínio ("Olá!", "Hoje tá frio, né?") não vira pergunta de FAQ:
    # senão "Hoje tá frio, né? Meu pedido 1002?" seria multi (hard-02 da v3).
    return DEFAULT_INTENT if r.has_subject(text) else None


def combine(intents: set[str]) -> tuple[str, float]:
    """Intenções das cláusulas → (intent da mensagem, confiança)."""
    if "human_request" in intents:
        return "human_request", 0.95
    if MULTI_PARTS <= intents:
        return "multi", 0.8
    intent = min(intents - {DEFAULT_INTENT} or {DEFAULT_INTENT})  # determinístico: assunto específico vence
    return intent, 0.85 if intent == "records" else 0.7


