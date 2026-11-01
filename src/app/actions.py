"""Mecânica das ferramentas: identidade do servidor, confirmação do dono, aprovação do operador e TTL.

As ferramentas do domínio ficam em `tools.py` (registro TOOLS: descrição, schema, função, `write`, `risk`).
Este módulo é genérico e não muda por desafio. Regras:
- `run_tool` nunca lança exceção (erro vira dado {ok, data, error}) e descarta `user_id` vindo do modelo;
- escrita não executa sem o dono confirmar (POST /actions/{id}/confirm);
- escrita de risco alto (`risk: "high"`, ex.: estorno, antecipação acima de limite) exige também a
  aprovação de um operador (quatro olhos): confirmar abre um caso `approval` na fila humana e só
  POST /actions/{id}/approve executa. Reject não executa nada;
- ação pendente expira em ACTION_TTL_MIN; toda transição é um UPDATE condicional (executa uma vez,
  também entre réplicas).
"""
from __future__ import annotations

import json
import time

from .observability import METRICS, span
from .schemas import ToolCall
from .store import get_store
from .tools import registry


def tool_specs() -> list[dict]:
    """Formato de tool use da Anthropic (name, description, input_schema)."""
    return [{"name": n, "description": t["description"], "input_schema": t["input_schema"]}
            for n, t in registry().items()]


def run_tool(name: str, args: dict, user_id: str, conversation_id: str | None = None,
             confirmed: bool = False) -> tuple[dict, ToolCall]:
    # user_id vindo do modelo é descartado já aqui: não entra no registro, na ação pendente nem na chamada.
    args = {k: v for k, v in (args or {}).items() if k != "user_id"}
    tool = registry().get(name)
    if tool is None:
        result = {"ok": False, "error": f"unknown_tool:{name}"}
    elif tool.get("write") and not confirmed:
        risk = tool.get("risk", "normal")
        action_id = get_store().create_action(conversation_id, user_id, name, args, risk=risk)
        # args no resultado: é a evidência dos números que o agente cita ("antecipar R$ 5.000,00") e o que o
        # cliente/operador confere antes de confirmar (Getnet v3, approval-06: valor sem base por faltar aqui)
        result = {"ok": False, "needs_confirmation": True, "action_id": action_id, "risk": risk, "args": args}
    else:
        with span("tool", tool=name):
            try:
                result = tool["fn"](**{**args, "user_id": user_id})  # identidade do servidor, nunca do modelo
            except Exception as exc:  # erro vira dado para o agente decidir
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    METRICS.inc("tool_calls_total", tool=name, ok=str(result.get("ok")).lower())
    return result, ToolCall(name=name, args=args, ok=bool(result.get("ok")))


def _expired(action: dict) -> bool:
    return bool(action.get("expires_at")) and time.time() > action["expires_at"]


def _execute(action: dict, frm: str) -> dict | None:
    """Executa se este chamador vencer a disputa `frm` → running. None = outro já executou."""
    store = get_store()
    if not store.claim_action(action["id"], frm=frm, to="running"):
        return None
    result, _ = run_tool(action["tool"], action["args"], action["user_id"], action["conversation_id"], confirmed=True)
    store.finish_action(action["id"], "done" if result.get("ok") else "failed", result)
    return result


def confirm_action(action_id: str, user_id: str) -> dict:
    """Confirmação do dono. Risco normal executa; risco alto vai para a fila do operador."""
    store = get_store()
    action = store.get_action(action_id)
    if not action or action["user_id"] != user_id:
        return {"ok": False, "error": "not_found"}
    if action["status"] != "pending":
        return {"ok": False, "error": f"already_{action['status']}"}
    if _expired(action):
        store.claim_action(action_id, frm="pending", to="expired")
        METRICS.inc("actions_expired_total")
        return {"ok": False, "error": "expired"}
    if action["risk"] == "high":
        if not store.claim_action(action_id, frm="pending", to="awaiting_operator"):
            return {"ok": False, "error": f"already_{store.get_action(action_id)['status']}"}
        summary = f"Aprovar {action['tool']} {json.dumps(action['args'], ensure_ascii=False)} para {user_id}"
        ticket = store.open_ticket(action["conversation_id"] or action_id, user_id, "approval", summary,
                                   priority="high", kind="approval", action_id=action_id)
        store.update_action(action_id, ticket_id=ticket["id"])
        METRICS.inc("approvals_requested_total", tool=action["tool"])
        from .notify import notify_new_ticket

        notify_new_ticket(ticket)
        return {"ok": False, "awaiting_operator": True, "ticket_id": ticket["id"]}
    result = _execute(action, frm="pending")
    if result is None:  # claim atômico: dois confirms simultâneos executam uma vez só
        return {"ok": False, "error": f"already_{store.get_action(action_id)['status']}"}
    return result


def decide_action(action_id: str, operator: str, approve: bool, note: str = "") -> dict | None:
    """Decisão do operador num caso de aprovação. None = ação inexistente ou já decidida."""
    store = get_store()
    action = store.get_action(action_id)
    if not action or action["status"] != "awaiting_operator":
        return None
    if approve:
        result = _execute(action, frm="awaiting_operator")
        if result is None:
            return None
        status = "aprovada e executada" if result.get("ok") else "aprovada, mas falhou"
    else:
        if not store.claim_action(action_id, frm="awaiting_operator", to="rejected"):
            return None
        result, status = None, "recusada"
    store.update_action(action_id, approved_by=operator)
    METRICS.inc("approvals_decided_total", tool=action["tool"], approved=str(approve).lower())
    if action.get("ticket_id"):
        store.close_ticket(action["ticket_id"], note or status, operator, status="answered")
    if action["conversation_id"]:  # o cliente vê o desfecho na conversa, sem precisar perguntar
        text = f"Sua solicitação ({action['tool']}) foi {status} por um atendente."
        if note:
            text += f" Observação: {note}"
        store.add_message(action["conversation_id"], action["user_id"], "assistant", text, "approval")
    return {"action": store.get_action(action_id), "result": result}


def __getattr__(attr: str):
    if attr == "TOOLS":  # compatibilidade: `actions.TOOLS` é o registro do domínio ativo
        return registry()
    raise AttributeError(attr)
