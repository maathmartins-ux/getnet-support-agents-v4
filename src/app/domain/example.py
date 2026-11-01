"""Domínio de EXEMPLO do kit (loja: pedidos e FAQ de entrega/troca). Os testes do kit usam SEMPRE este módulo.

NÃO edite este arquivo no relógio. O pré-voo copia-o para `challenge.py`; o desafio edita só a cópia.
Cada nome abaixo é parte do contrato lido pelo motor (ver `app/domain/__init__.py`).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.config import data_dir

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

# --- portão "sem fonte, sem afirmação" ----------------------------------------------------------------------
# BM25 bruto abaixo disso = sem fonte. O valor do desafio sai de `run_evals --calibrate-gate` e vai para o
# challenge.py (ou COVERAGE_MIN_SCORE no compose). Getnet v3: trocar o padrão do KIT quebrou 14 testes.
MIN_SCORE = 0.5

# --- prompts ------------------------------------------------------------------------------------------------
# None = prompts do kit (src/app/prompts). O pré-voo aponta o challenge para src/app/domain/challenge_prompts.
PROMPTS_DIR = None

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


# --- ferramentas (registro lido por actions.py e mcp_server.py) ---------------------------------------------
# Contrato de cada função: recebe os argumentos do schema + `user_id` (sempre o autenticado, injetado pelo
# servidor) e devolve {ok, data} ou {ok: False, error}. Mesma resposta para inexistente e de outro dono.
_ID = {"type": "string", "pattern": r"^\d{4}$", "description": "Número do registro (4 dígitos)"}


@lru_cache(maxsize=4)
def _records(path: str) -> dict:
    return json.loads(open(path, encoding="utf-8").read())


def records() -> dict:
    """Adaptador do sistema de registro: hoje data/records.json; em produção, a API do CRM (mesma assinatura)."""
    return _records(str(data_dir() / "records.json"))


def _lookup(record_id: str, user_id: str) -> dict:
    rec = records().get(record_id)
    if not rec or rec["owner"] != user_id:  # mesma resposta para inexistente e de outro dono
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "data": {"record_id": record_id, **{k: v for k, v in rec.items() if k != "owner"}}}


def _cancel(record_id: str, user_id: str) -> dict:
    found = _lookup(record_id, user_id)
    if not found["ok"]:
        return found
    return {"ok": True, "data": {"record_id": record_id, "status": "cancel_requested"}}


def _refund(record_id: str, user_id: str) -> dict:
    found = _lookup(record_id, user_id)
    if not found["ok"]:
        return found
    return {"ok": True, "data": {"record_id": record_id, "status": "refund_issued"}}


_ONE_ID = {"type": "object", "properties": {"record_id": _ID}, "required": ["record_id"],
           "additionalProperties": False}
TOOLS = {
    "lookup_record": {"description": "Consulta um registro do cliente autenticado.", "write": False,
                      "input_schema": _ONE_ID, "fn": _lookup},
    "cancel_record": {"description": "Solicita cancelamento. Exige confirmação do cliente.", "write": True,
                      "input_schema": _ONE_ID, "fn": _cancel},
    # risco alto: move dinheiro → confirmação do dono E aprovação de um operador (actions.py)
    "refund_record": {"description": "Solicita estorno. Exige confirmação do cliente e aprovação de um atendente.",
                      "write": True, "risk": "high", "input_schema": _ONE_ID, "fn": _refund},
}
# Ação de escrita -> status do item em que ela faz sentido. Getnet v3: contestar escolhia a ÚLTIMA transação,
# mesmo recusada; ali a regra seria {"open_chargeback": {"aprovada"}}. Aqui: estorno só de pedido que já saiu.
ELIGIBLE_STATUS: dict[str, set[str]] = {"refund_record": {"enviado", "entregue"}}

# --- caminho offline do agente records (sem LLM / provedor fora) -------------------------------------------
_REC_ID = re.compile(r"\b(\d{4})\b")
_CANCEL = re.compile(r"\bcancel", re.IGNORECASE)
_REFUND = re.compile(r"\b(estorn|reembols|refund|devolu[cç][aã]o do (valor|dinheiro))", re.IGNORECASE)


def _record_id(ctx) -> str | None:
    found = _REC_ID.search(ctx.text)
    if found:
        return found.group(1)
    for m in reversed(ctx.history):  # memória: "e pode cancelar ele?" usa o registro citado antes
        found = _REC_ID.search(m["content"])
        if found:
            return found.group(1)
    older = _REC_ID.findall(ctx.conversation_digest)  # citado antes da janela recente: o mais recente vale
    return older[-1] if older else None


def records_offline(ctx, a):
    """Atendimento determinístico do agente records. `a` = módulo app.agents (AgentResult, run_tool, _t...)."""
    AgentResult, run_tool, t = a.AgentResult, a.run_tool, a._t
    rid = _record_id(ctx)
    if not rid:
        return AgentResult("records", "ok", text=t(ctx, "Qual o número do pedido?", "What is the order number?"))
    tool = ("refund_record" if _REFUND.search(ctx.text) else
            "cancel_record" if _CANCEL.search(ctx.text) else "lookup_record")
    calls: list = []
    ev: list[str] = []
    if tool in ELIGIBLE_STATUS:  # ação com regra de status: consulta antes de propor
        found, lookup = run_tool("lookup_record", {"record_id": rid}, ctx.user_id, ctx.conversation_id)
        calls.append(lookup)
        ev.append(json.dumps(found, ensure_ascii=False))
        if found.get("ok") and a.latest_eligible([found["data"]], tool) is None:
            status = found["data"]["status"]
            return AgentResult("records", "ok", tool_calls=calls, evidence=ev,
                               facts={"record_id": rid, "status": status, "ineligible": tool},
                               text=t(ctx, f'O pedido {rid} está "{status}": essa ação ainda não se aplica a ele. '
                                           "Nada foi feito.",
                                      f'Order {rid} is "{status}": this action does not apply to it yet. '
                                      "Nothing has been done."))
    result, call = run_tool(tool, {"record_id": rid}, ctx.user_id, ctx.conversation_id)
    calls.append(call)
    ev.append(json.dumps(result, ensure_ascii=False))
    if result.get("needs_confirmation"):
        aid = result["action_id"]
        verb_pt, verb_en = ("estornar", "refund") if tool == "refund_record" else ("cancelar", "cancel")
        extra_pt = " Depois da sua confirmação, um atendente aprova." if result.get("risk") == "high" else ""
        extra_en = " After you confirm, a human agent approves it." if result.get("risk") == "high" else ""
        return AgentResult("records", "ok", tool_calls=calls, pending_action=aid, facts={"record_id": rid},
                           evidence=ev,
                           text=t(ctx, f"Para {verb_pt} o pedido {rid}, confirme a ação {aid}.{extra_pt} "
                                       "Nada foi feito ainda.",
                                  f"To {verb_en} order {rid}, confirm action {aid}.{extra_en} "
                                  "Nothing has been done yet."))
    if result.get("error") == "not_found":
        return AgentResult("records", "ok", tool_calls=calls, facts={"record_not_found": rid}, evidence=ev,
                           text=t(ctx, f"Não encontrei o pedido {rid} na sua conta.",
                                  f"I couldn't find order {rid} in your account."))
    if not result.get("ok"):
        return AgentResult("records", "error", tool_calls=calls, reason="tool_error", evidence=ev)
    data = result["data"]
    return AgentResult("records", "ok", tool_calls=calls, facts={"record_id": rid, "status": data["status"]},
                       evidence=ev,
                       text=t(ctx, f"Pedido {rid} ({data['item']}): {data['status']}.",
                              f"Order {rid} ({data['item']}): {data['status']}."))
