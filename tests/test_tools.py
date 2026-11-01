"""Contrato das 6 ferramentas do Customer Support Agent (SPEC, frente T) e do caminho offline do records.

Identidade do servidor, mesma resposta para inexistente e de outro dono, liquidação em dias úteis, contestação
só de transação aprovada, escrita vira pending_action (risco alto: confirmação + operador).
"""
import json
import re
from datetime import date

import pytest

from app import actions, agents, domain, grounding


@pytest.fixture
def T(domain_data):
    return domain.active()


def ctx(text, language="pt", user="cliente1988"):
    return agents.Context(text=text, language=language, user_id=user, conversation_id="c-tools")


# --- registro e schemas ---------------------------------------------------------------------------------------
def test_registry_has_exactly_the_six_spec_tools(T):
    assert set(T.TOOLS) == {"get_settlements", "get_transactions", "get_terminal_status", "restart_terminal",
                            "request_anticipation", "open_chargeback"}
    writes = {n for n, t in T.TOOLS.items() if t["write"]}
    assert writes == {"restart_terminal", "request_anticipation", "open_chargeback"}
    assert {n for n, t in T.TOOLS.items() if t.get("risk") == "high"} == {"request_anticipation", "open_chargeback"}
    assert T.ELIGIBLE_STATUS == {"open_chargeback": {"aprovada"}}
    for t in T.TOOLS.values():
        s = t["input_schema"]
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert "user_id" not in s["properties"]  # identidade nunca é argumento do modelo


def test_id_patterns(T):
    tx = T.TOOLS["open_chargeback"]["input_schema"]["properties"]["transaction_id"]["pattern"]
    term = T.TOOLS["restart_terminal"]["input_schema"]["properties"]["terminal_id"]["pattern"]
    assert re.match(tx, "TX1004") and not re.match(tx, "1004")
    assert re.match(term, "T1988B") and not re.match(term, "T1988")


# --- identidade -----------------------------------------------------------------------------------------------
def test_identity_comes_from_server_not_from_model_args(T):
    res, _ = actions.run_tool("get_terminal_status", {"user_id": "cliente2024"}, "cliente1988")
    assert {x["terminal_id"] for x in res["data"]["terminals"]} == {"T1988A", "T1988B"}


def test_other_owner_and_missing_give_same_answer(T):
    other, _ = actions.run_tool("get_transactions", {"transaction_id": "TX2001"}, "cliente1988")
    missing, _ = actions.run_tool("get_transactions", {"transaction_id": "TX9999"}, "cliente1988")
    assert other == missing == {"ok": False, "error": "not_found"}
    term, _ = actions.run_tool("get_terminal_status", {"terminal_id": "T2024A"}, "cliente1988")
    assert term == {"ok": False, "error": "not_found"}
    assert T.TOOLS["restart_terminal"]["fn"](user_id="cliente1988", terminal_id="T2024A") == term


def test_crm_ids_from_spec(T):
    tx = {t["transaction_id"]: t for t in T.TOOLS["get_transactions"]["fn"](user_id="cliente1988")["data"]
          ["transactions"]}
    assert tx["TX1004"]["status"] == "aprovada" and tx["TX1004"]["amount"] == 350.0 and tx["TX1004"]["contestable"]
    assert (tx["TX1007"]["decline_code"], tx["TX1008"]["decline_code"]) == ("51", "05")
    terms = {t["terminal_id"]: t for t in T.TOOLS["get_terminal_status"]["fn"](user_id="cliente1988")["data"]
             ["terminals"]}
    assert terms["T1988A"]["online"] and terms["T1988B"]["signal"] == "sem_sinal"
    other = T.TOOLS["get_transactions"]["fn"](user_id="cliente2024")["data"]["transactions"]
    assert {t["transaction_id"]: t["status"] for t in other} == {"TX2001": "aprovada", "TX2002": "recusada"}


# --- dias úteis -----------------------------------------------------------------------------------------------
def test_settlement_business_days(T):
    sat, fri = date(2026, 10, 31), date(2026, 11, 6)
    assert T.settlement_date(sat, "pix") == date(2026, 11, 3)  # sábado; 02/11 é feriado (Finados)
    assert T.settlement_date(sat, "debito") == date(2026, 11, 3)
    assert T.settlement_date(fri, "debito") == date(2026, 11, 9)  # sexta → segunda
    assert T.settlement_date(fri, "pix") == fri
    assert T.settlement_date(sat, "credito") == date(2026, 11, 30)
    assert T.settlement_date(date(2026, 11, 7), "credito") == date(2026, 12, 7)  # 07/12 segunda
    assert T.business_day(T.settlement_date(date(2026, 11, 14), "credito"))


def test_settlements_yesterday_has_debit_and_credit_and_available(T):
    d = T.TOOLS["get_settlements"]["fn"](user_id="cliente1988", period="yesterday")["data"]
    assert {"debito", "credito"} <= set(d["by_method"])
    assert d["total"] == round(sum(i["amount"] for i in d["items"]), 2)
    assert all(T.business_day(date.fromisoformat(i["expected_date"])) for i in d["items"])
    assert d["available_for_anticipation"] > 0


# --- escrita --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,args,risk", [("restart_terminal", {"terminal_id": "T1988B"}, "normal"),
                                            ("request_anticipation", {}, "high"),
                                            ("open_chargeback", {"transaction_id": "TX1004"}, "high")])
def test_write_becomes_pending_action(T, name, args, risk):
    res, call = actions.run_tool(name, args, "cliente1988", "c1")
    assert res["needs_confirmation"] and res["action_id"] and res["risk"] == risk and not call.ok


def test_chargeback_only_for_approved(T):
    fn = T.TOOLS["open_chargeback"]["fn"]
    assert fn(user_id="cliente1988", transaction_id="TX1007") == {"ok": False, "error": "not_eligible",
                                                                  "status": "recusada"}
    assert fn(user_id="cliente1988", transaction_id="TX1004")["ok"]
    assert fn(user_id="cliente1988", transaction_id="TX2001") == {"ok": False, "error": "not_found"}


def test_anticipation_amount_defaults_to_available_and_is_capped(T):
    fn = T.TOOLS["request_anticipation"]["fn"]
    avail = T.TOOLS["get_settlements"]["fn"](user_id="cliente1988")["data"]["available_for_anticipation"]
    assert fn(user_id="cliente1988")["data"]["amount"] == avail
    assert fn(user_id="cliente1988", amount=avail + 1)["error"] == "amount_exceeds_available"


# --- caminho offline ------------------------------------------------------------------------------------------
def _grounded(r):
    assert grounding.unsupported_numbers(r.text, r.evidence) == [], r.text


@pytest.mark.parametrize("lang,msg,word", [("en", "When will the money from yesterday's sales be deposited?",
                                            "Yesterday's sales"),
                                           ("pt", "Quando cai o dinheiro das vendas de ontem?", "vendas de ontem")])
def test_offline_settlements_sentence(T, lang, msg, word):
    r = agents.records_agent(ctx(msg, lang))
    assert [c.name for c in r.tool_calls] == ["get_settlements"] and word in r.text and "R$" in r.text
    _grounded(r)


@pytest.mark.parametrize("lang,msg,word", [("en", "My card machine won't connect to the internet, what should I do?",
                                            "no signal"),
                                           ("pt", "Minha maquininha não conecta na internet", "sem sinal")])
def test_offline_terminal_sentence_and_facts(T, lang, msg, word):
    r = agents.records_agent(ctx(msg, lang))
    assert [c.name for c in r.tool_calls] == ["get_terminal_status"]
    assert "T1988B" in r.text and "Get Clássica" in r.text and word in r.text
    assert r.facts["terminal_id"] == "T1988B" and r.facts["signal"] == "sem_sinal"
    _grounded(r)


def test_offline_decline_reads_transactions(T):
    r = agents.records_agent(ctx("My card machine is showing a transaction decline error.", "en"))
    assert [c.name for c in r.tool_calls] == ["get_transactions"] and "51" in r.text and "05" in r.text
    assert "declined" in r.text
    _grounded(r)


@pytest.mark.parametrize("lang,msg,tool,phrase", [
    ("pt", "Quero antecipar todo o valor disponível das minhas vendas.", "request_anticipation", "Nada foi feito"),
    ("en", "Please request an anticipation of my available receivables.", "request_anticipation", "Nothing has been"),
    ("pt", "Reinicie a maquininha T1988B, por favor.", "restart_terminal", "Nada foi feito"),
    ("en", "Restart my card machine T1988B.", "restart_terminal", "Nothing has been"),
    ("pt", "Quero contestar a venda TX1004, não reconheço.", "open_chargeback", "Nada foi feito"),
    ("en", "Open a chargeback for transaction TX1004.", "open_chargeback", "Nothing has been"),
])
def test_offline_approval_proposes_and_never_executes(T, lang, msg, tool, phrase):
    r = agents.records_agent(ctx(msg, lang))
    assert r.tool_calls[-1].name == tool and r.pending_action and r.pending_action in r.text and phrase in r.text
    if T.TOOLS[tool].get("risk") == "high":
        assert ("atendente" if lang == "pt" else "human agent") in r.text
    assert r.tool_calls[0].name != tool  # consultou antes de propor
    _grounded(r)


def test_offline_chargeback_of_declined_is_refused(T):
    r = agents.records_agent(ctx("Quero contestar a transação TX1007", "pt"))
    assert r.pending_action is None and [c.name for c in r.tool_calls] == ["get_transactions"]
    assert "recusada" in r.text and r.facts["ineligible"] == "open_chargeback"


def test_offline_anticipation_above_available_is_refused(T):
    r = agents.records_agent(ctx("Antecipe R$ 900.000 das minhas vendas", "pt"))
    assert r.pending_action is None and "Nada foi feito" in r.text
    _grounded(r)


def test_offline_restart_without_id_picks_the_terminal_with_problem(T):
    r = agents.records_agent(ctx("Reinicia minha maquininha", "pt"))
    assert r.pending_action and json.dumps(r.tool_calls[-1].args) == '{"terminal_id": "T1988B"}'


def test_confirm_runs_normal_risk_and_high_risk_waits_for_operator(T):
    restart, _ = actions.run_tool("restart_terminal", {"terminal_id": "T1988B"}, "cliente1988", "c1")
    assert actions.confirm_action(restart["action_id"], "cliente2024") == {"ok": False, "error": "not_found"}
    assert actions.confirm_action(restart["action_id"], "cliente1988")["data"]["status"] == "restart_sent"
    cb, _ = actions.run_tool("open_chargeback", {"transaction_id": "TX1004"}, "cliente1988", "c1")
    waiting = actions.confirm_action(cb["action_id"], "cliente1988")
    assert waiting["awaiting_operator"] and waiting["ticket_id"]
    done = actions.decide_action(cb["action_id"], "op", approve=True)
    assert done["result"]["data"]["status"] == "chargeback_opened"


def test_offline_chargeback_uses_transaction_from_history(T):
    c = ctx("Quero contestar ela, não reconheço", "pt")
    c.history = [{"role": "user", "content": "O que é a transação TX1004?"}]
    r = agents.records_agent(c)
    assert r.pending_action and r.tool_calls[-1].args == {"transaction_id": "TX1004"}


def test_offline_single_approved_transaction_pt(T):
    r = agents.records_agent(ctx("Me mostra a transação TX1004", "pt"))
    assert "TX1004" in r.text and "aprovada" in r.text and "R$ 350,00" in r.text and r.pending_action is None
    _grounded(r)
