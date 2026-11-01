"""Item 2 — agentes independentes em paralelo, com resultado em ordem determinística."""
import time

from fakes import fake_llm, resp, text, tool_use
from fastapi.testclient import TestClient

from app import domain, supervisor
from app.agents import AgentResult, Context
from app.api import app
from app.auth import issue_token
from app.observability import new_trace_id, record_llm_usage, request_usage

SLEEP = 0.3


def ctx(text_="x"):
    return Context(text=text_, language="pt", user_id="u1", conversation_id="c1", intent="multi")


def slow(name, seconds, cost=False):
    def agent(c):
        time.sleep(seconds)
        if cost:
            record_llm_usage("claude-sonnet-5-5", 1000, 100)  # dentro da thread
        return AgentResult(name, "ok", text=name, facts={name: True})
    return agent


def test_independent_agents_run_in_parallel_in_plan_order(monkeypatch):
    monkeypatch.setitem(supervisor.AGENTS, "faq", slow("faq", SLEEP + 0.1))  # termina por último
    monkeypatch.setitem(supervisor.AGENTS, "records", slow("records", SLEEP))
    t0 = time.perf_counter()
    out = supervisor.run(["faq", "records"], ctx())
    elapsed = time.perf_counter() - t0
    assert elapsed < 2 * SLEEP, f"sem paralelismo: {elapsed:.2f}s"  # em série seriam 0,7 s
    assert out.route == ["faq", "records"] and [r.text for r in out.results] == ["faq", "records"]


def test_parallel_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_PARALLEL", "false")
    monkeypatch.setitem(supervisor.AGENTS, "faq", slow("faq", SLEEP))
    monkeypatch.setitem(supervisor.AGENTS, "records", slow("records", SLEEP))
    t0 = time.perf_counter()
    supervisor.run(["faq", "records"], ctx())
    assert time.perf_counter() - t0 >= 2 * SLEEP


def test_cost_from_threads_lands_in_the_request(monkeypatch):
    new_trace_id()
    monkeypatch.setitem(supervisor.AGENTS, "faq", slow("faq", 0.05, cost=True))
    monkeypatch.setitem(supervisor.AGENTS, "records", slow("records", 0.05, cost=True))
    supervisor.run(["faq", "records"], ctx())
    assert request_usage()["input_tokens"] == 2000  # contextvars copiados para cada thread


def test_batches_respect_dependencies_and_terminal_escalation(monkeypatch):
    assert supervisor.next_batch(["faq", "records", "escalation"], 4) == ["faq", "records"]
    assert supervisor.next_batch(["escalation", "faq"], 4) == ["escalation"]
    monkeypatch.setattr(domain.active(), "DEPENDS", {"records": {"faq"}})  # dependência é do domínio
    assert supervisor.next_batch(["faq", "records"], 4) == ["faq"]


def test_llm_parallel_tool_calls_return_in_block_order(monkeypatch):
    monkeypatch.setitem(supervisor.AGENTS, "faq", slow("faq", SLEEP + 0.1))
    monkeypatch.setitem(supervisor.AGENTS, "records", slow("records", SLEEP))
    llm, msgs = fake_llm([
        resp("tool_use", tool_use("faq_agent", {"query": "prazo"}, "a"),
             tool_use("records_agent", {"request": "1002"}, "b")),
        resp("end_turn", text("Prazo e pedido respondidos.")),
    ])
    monkeypatch.setattr(supervisor, "get_llm", lambda role="agent": llm)
    t0 = time.perf_counter()
    out = supervisor.run(["faq", "records"], ctx())
    assert time.perf_counter() - t0 < 2 * SLEEP
    assert out.route == ["faq", "records"] and not out.replans
    results = msgs.sent[1]["messages"][-1]["content"]  # todos os tool_result numa mensagem só
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_compound_question_runs_both_agents_via_http():
    body = TestClient(app).post("/chat", json={"message": "Qual o prazo de entrega? E onde está meu pedido 1002?"},
                                headers={"Authorization": f"Bearer {issue_token('u1')}"}).json()
    assert body["intent"] == "multi" and body["route"] == ["router", "faq", "records"]
    assert body["sources"][0]["id"] == "faq-entrega" and body["tool_calls"][0]["name"] == "lookup_record"
    assert "Livro B" in body["answer"] and "3 a 5 dias" in body["answer"]
