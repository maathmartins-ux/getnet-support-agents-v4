"""Defeitos de runtime dos agentes achados na auditoria da Getnet v3 (31/10/2026).

4. Busca web: a v3 fazia `evidence=[text]` (a própria resposta como evidência) e a checagem de números
   ficava vazia; o prompt não tinha a data de hoje ("tomorrow (Oct 30)" num dia 31).
5. Busca entre idiomas: "How many installments in Crediário?" não achava o documento em português.
6. Escolha do item para a ação: a v3 contestava a ÚLTIMA transação, mesmo recusada.
"""
import json
from datetime import date
from types import SimpleNamespace as NS

from fakes import fake_llm, resp, text

from app import agents
from app.agents import Context
from app.grounding import unsupported_numbers
from app.store import get_store


def ctx(text_, intent="faq", language="pt", user="u1", conv="c-rt"):
    return Context(text=text_, language=language, user_id=user, conversation_id=conv, intent=intent)


# --- 4. busca web ---------------------------------------------------------------------------------
def _web_reply(answer: str):
    search = NS(type="server_tool_use", id="srv1", name="web_search", input={"query": "cotação dólar hoje"})
    results = NS(type="web_search_tool_result", tool_use_id="srv1", content=[
        NS(type="web_search_result", url="https://www.bcb.gov.br/cotacoes", title="Cotações - Banco Central",
           page_age="31 de outubro de 2026", encrypted_content="opaco")])
    cited = NS(type="text", text=answer, citations=[NS(
        type="web_search_result_location", url="https://www.bcb.gov.br/cotacoes", title="Cotações - Banco Central",
        cited_text="Dólar comercial: venda R$ 5,43 em 31/10/2026", encrypted_index="opaco")])
    return resp("end_turn", search, results, cited)


def test_web_answer_evidence_is_the_search_result_not_the_answer(monkeypatch):
    answer = "O dólar comercial fechou a R$ 5,43 hoje, com alta de 2,1% (Banco Central)."
    llm, msgs = fake_llm([_web_reply(answer)])
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    r = agents.faq_agent(ctx("Quanto está o dólar hoje?", intent="general"))
    assert r.status == "ok" and r.text == answer
    assert answer not in r.evidence  # a resposta não sustenta a si mesma
    assert [s.url for s in r.sources] == ["https://www.bcb.gov.br/cotacoes"]
    # 5,43 veio da busca; 2,1% não veio de lugar nenhum e agora aparece
    assert unsupported_numbers(r.text, r.evidence) == ["2,1"]
    assert msgs.sent[0]["tools"][0]["name"] == "web_search"


def test_web_search_error_leaves_no_evidence(monkeypatch):
    search = NS(type="server_tool_use", id="srv1", name="web_search", input={"query": "x"})
    failed = NS(type="web_search_tool_result", tool_use_id="srv1",
                content=NS(type="web_search_tool_result_error", error_code="max_uses_exceeded"))
    llm, _ = fake_llm([resp("end_turn", search, failed, text("O dólar está R$ 5,43."))])
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    r = agents.faq_agent(ctx("Quanto está o dólar hoje?", intent="general"))
    assert r.evidence == [] and unsupported_numbers(r.text, r.evidence) == ["5,43"]


def test_general_prompt_carries_today(monkeypatch):
    llm, msgs = fake_llm([_web_reply("ok")])
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    agents.faq_agent(ctx("Vai chover amanhã em São Paulo?", intent="general"))
    assert date.today().isoformat() in msgs.sent[0]["system"]


def test_general_offline_is_honest_and_has_no_number():
    r = agents.faq_agent(ctx("Quanto está o dólar hoje?", intent="general"))
    assert r.status == "ok" and r.facts == {"web_search": False}
    assert unsupported_numbers(r.text, r.evidence) == []


# --- 5. busca entre idiomas -----------------------------------------------------------------------
def test_english_question_finds_portuguese_doc_offline():
    r = agents.faq_agent(ctx("What is the delivery deadline?", language="en"))
    assert r.status == "ok", r.reason
    assert r.sources[0].id == "faq-entrega"


def test_english_question_reaches_the_llm_with_the_portuguese_passage(monkeypatch):
    llm, msgs = fake_llm([resp("end_turn", text("Delivery takes 3 to 5 business days [1]."))])
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    r = agents.faq_agent(ctx("How long does shipping take?", language="en"))
    assert r.status == "ok" and r.sources[0].id == "faq-entrega"
    assert "Prazo de entrega" in msgs.sent[0]["messages"][-1]["content"]


def test_expand_query_adds_the_other_language_and_ignores_partial_words():
    out = agents.expand_query("What are the support hours?")
    assert "atendimento" in out and "horário" in out
    assert agents.expand_query("Qual o prazo de entrega?").startswith("Qual o prazo de entrega?")
    assert "delivery" in agents.expand_query("Qual o prazo de entrega?")
    assert agents.expand_query("supportive") == "supportive"  # palavra inteira, não pedaço


def test_missing_glossary_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert agents.expand_query("What is the delivery deadline?") == "What is the delivery deadline?"


# --- 6. item elegível para a ação -----------------------------------------------------------------
TXS = [{"transaction_id": "TX-1", "status": "approved", "amount": 120.0},
       {"transaction_id": "TX-2", "status": "approved", "amount": 80.0},
       {"transaction_id": "TX-3", "status": "declined", "amount": 250.0}]


def test_latest_eligible_skips_items_whose_status_does_not_fit_the_action(monkeypatch):
    monkeypatch.setitem(agents.ELIGIBLE_STATUS, "open_chargeback_dispute", {"approved"})
    assert agents.latest_eligible(TXS, "open_chargeback_dispute")["transaction_id"] == "TX-2"
    assert agents.latest_eligible(TXS[2:], "open_chargeback_dispute") is None  # só recusada: nada a contestar
    assert agents.latest_eligible(TXS, "tool_sem_regra")["transaction_id"] == "TX-3"  # sem regra: o último


def test_refund_of_a_record_not_yet_shipped_is_not_proposed():
    r = agents.records_agent(ctx("Quero o estorno do pedido 1002"))  # 1002 está "em separação"
    assert r.pending_action is None and r.status == "ok"
    assert r.facts == {"record_id": "1002", "status": "em separação", "ineligible": "refund_record"}
    assert "em separação" in r.text and unsupported_numbers(r.text, r.evidence, "estorno do pedido 1002") == []
    assert [c.name for c in r.tool_calls] == ["lookup_record"]
    with get_store()._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0


def test_refund_of_a_delivered_record_is_still_proposed():
    r = agents.records_agent(ctx("Quero o estorno do pedido 1001"))  # "entregue"
    assert r.pending_action and json.loads(r.evidence[-1])["args"] == {"record_id": "1001"}
