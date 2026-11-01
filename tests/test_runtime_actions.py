"""Ação pendente leva os argumentos propostos no resultado (evidência dos números e leitura do operador).

Getnet v3 (31/10/2026), approval-06: "Preparei a proposta para antecipar R$ 5.000,00 (ação A-...)" foi marcado
como número sem base. O valor era o argumento da tool, mas o resultado pendente só tinha
{needs_confirmation, action_id, risk}: a evidência (json do resultado) não continha o 5000.
"""
import json

from app import actions
from app.actions import run_tool
from app.grounding import unsupported_numbers


def _anticipate(amount: float, user_id: str) -> dict:
    return {"ok": True, "data": {"amount": amount, "status": "requested"}}


def _with_money_tool(monkeypatch):
    monkeypatch.setitem(actions.TOOLS, "request_anticipation", {
        "description": "Antecipa recebíveis.", "write": True, "risk": "high", "fn": _anticipate,
        "input_schema": {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}})


def test_pending_result_carries_the_proposed_args(monkeypatch):
    _with_money_tool(monkeypatch)
    result, call = run_tool("request_anticipation", {"amount": 5000.0, "user_id": "intruso"}, "u1", "c1")
    assert result["needs_confirmation"] is True
    assert result["args"] == {"amount": 5000.0}  # user_id do modelo continua descartado
    assert call.args == {"amount": 5000.0}


def test_number_quoted_from_the_proposed_args_is_grounded(monkeypatch):
    _with_money_tool(monkeypatch)
    result, _ = run_tool("request_anticipation", {"amount": 5000.0}, "u1", "c1")
    answer = f"Preparei a proposta para antecipar R$ 5.000,00 dos seus recebíveis (ação {result['action_id']})."
    assert unsupported_numbers(answer, [json.dumps(result, ensure_ascii=False)]) == []


def test_offline_records_pending_evidence_has_the_record_id():
    from app.agents import Context, records_agent

    res = records_agent(Context(text="quero cancelar o pedido 1002", language="pt", user_id="u1",
                                conversation_id="c-args"))
    assert res.pending_action
    assert json.loads(res.evidence[0])["args"] == {"record_id": "1002"}
