"""Agentes (caminho offline e LLM com cliente falso), tools e custo por request."""
import json

from fakes import fake_llm, resp, text, tool_use

from app import agents
from app.actions import run_tool
from app.agents import Context
from app.observability import new_trace_id, record_llm_usage, request_usage


def ctx(text_, intent="faq", user="u1"):
    return Context(text=text_, language="pt", user_id=user, conversation_id="c1", intent=intent)


def test_tool_identity_is_server_side():
    result, call = run_tool("lookup_record", {"record_id": "2001", "user_id": "u2"}, user_id="u1")
    assert result == {"ok": False, "error": "not_found"}  # args do modelo não trocam a identidade
    assert call.args == {"record_id": "2001"}  # e o user_id do modelo nem fica registrado


def test_unknown_tool_never_raises():
    result, call = run_tool("rm_rf", {}, user_id="u1")
    assert result["ok"] is False and call.ok is False


def test_cost_per_request_from_price_table():
    new_trace_id()
    cost = record_llm_usage("claude-sonnet-5-5", 1_000_000, 100_000)
    assert cost == 2.0 + 1.0  # US$2/MTok entrada + US$10/MTok saída × 0,1
    assert request_usage() == {"input_tokens": 1_000_000, "output_tokens": 100_000, "cost_usd": 3.0}
    assert record_llm_usage("modelo-sem-preco", 10, 10) == 0.0


def test_faq_llm_not_in_sources_is_insufficient(monkeypatch):
    llm, msgs = fake_llm([resp("end_turn", text("NOT_IN_SOURCES"))], effort="medium")
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    r = agents.faq_agent(ctx("Qual o prazo de entrega para a Lua?"))
    assert r.status == "insufficient" and r.reason == "not_in_sources"
    assert msgs.sent[0]["output_config"] == {"effort": "medium"}


def test_records_llm_tool_loop_enforces_server_identity(monkeypatch):
    llm, msgs = fake_llm([
        resp("tool_use", tool_use("lookup_record", {"record_id": "2001", "user_id": "u2"})),
        resp("end_turn", text("Não encontrei esse pedido na sua conta.")),
    ], effort="medium")
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    r = agents.records_agent(ctx("Mostre o pedido 2001", "records"))
    assert r.tool_calls[0].args == {"record_id": "2001"}  # user_id do modelo descartado
    result = json.loads(msgs.sent[1]["messages"][-1]["content"][0]["content"])
    assert result == {"ok": False, "error": "not_found"} and "Livro C" not in json.dumps(msgs.sent[1], default=str)
    assert r.status == "insufficient"  # nenhuma tool deu certo: o supervisor decide o próximo passo


def test_records_llm_failure_uses_deterministic_path(monkeypatch):
    from app.llm import LLMError

    llm, _ = fake_llm([LLMError("connection", "connection")])
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    r = agents.records_agent(ctx("Onde está meu pedido 1002?", "records"))
    assert r.status == "ok" and "Livro B" in r.text


def test_faq_llm_sees_its_own_part_of_a_compound_question(monkeypatch):
    # smoke ao vivo de 31/10/2026: "prazo? E onde está meu pedido 1002?" — o FAQ via a mensagem inteira,
    # não conseguia responder o pedido, devolvia NOT_IN_SOURCES e a conversa ia para humano sem precisar
    llm, msgs = fake_llm([resp("end_turn", text("Capitais: 3 a 5 dias úteis [1]."))], effort="medium")
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    c = ctx("Qual o prazo de entrega? E onde está meu pedido 1002?")
    c.facts["ask"] = {"faq": "Qual o prazo de entrega?"}
    r = agents.faq_agent(c)
    prompt = msgs.sent[0]["messages"][-1]["content"]
    assert "Parte que cabe a você: Qual o prazo de entrega?" in prompt and "pedido 1002" in prompt
    assert r.status == "ok" and "outro especialista" in msgs.sent[0]["system"]


def test_faq_without_sub_question_keeps_the_plain_prompt(monkeypatch):
    llm, msgs = fake_llm([resp("end_turn", text("3 a 5 dias úteis [1]."))], effort="medium")
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    agents.faq_agent(ctx("Qual o prazo de entrega?"))
    prompt = msgs.sent[0]["messages"][-1]["content"]
    assert "Pergunta: Qual o prazo de entrega?" in prompt and "Parte que cabe" not in prompt


def test_records_llm_sees_its_own_part_of_a_compound_question(monkeypatch):
    llm, msgs = fake_llm([resp("end_turn", text("Pedido 1002: em separação."))], effort="medium")
    monkeypatch.setattr(agents, "get_llm", lambda role="agent": llm)
    c = ctx("Qual o prazo de entrega? E onde está meu pedido 1002?", "records")
    c.facts["ask"] = {"records": "status do pedido 1002"}
    agents.records_agent(c)
    assert "Parte que cabe a você: status do pedido 1002" in msgs.sent[0]["messages"][-1]["content"]
    assert "outro\nespecialista" in msgs.sent[0]["system"] or "outro especialista" in msgs.sent[0]["system"]
