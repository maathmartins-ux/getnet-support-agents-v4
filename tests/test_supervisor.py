"""Item 1 — supervisor: tabela determinística (offline) e LLM com tool loop que lê status e replaneja."""
import json

from fakes import fake_llm, resp, text, tool_use

from app import supervisor
from app.agents import AgentResult, Context
from app.config import effort_for, model_for
from app.llm import LLMError
from app.observability import new_trace_id, request_usage


def ctx(text_, intent="faq", user="u1", conv="c1"):
    return Context(text=text_, language="pt", user_id=user, conversation_id=conv, intent=intent)


def use_llm(monkeypatch, llm):
    monkeypatch.setattr(supervisor, "get_llm", lambda role="agent": llm)


# --- tabela (caminho do CI) -------------------------------------------------------------------
def test_table_replans_when_no_source():
    out = supervisor.run(["faq"], ctx("Qual a capital da Mongólia?"))
    assert out.engine == "table" and out.route == ["faq", "escalation"]
    assert out.replans == ["faq:no_source->escalation"]
    assert out.results[-1].handoff.reason == "faq_no_source"


def test_table_tool_error_escalates(monkeypatch):
    monkeypatch.setitem(supervisor.AGENTS, "records", lambda c: AgentResult("records", "error", reason="tool_error"))
    out = supervisor.run(["records"], ctx("pedido 1002", "records"))
    assert out.route == ["records", "escalation"] and out.replans == ["records:tool_error->escalation"]


def test_agent_exception_becomes_error_status(monkeypatch):
    def boom(c):
        raise RuntimeError("disco cheio")

    monkeypatch.setitem(supervisor.AGENTS, "records", boom)
    out = supervisor.run(["records"], ctx("pedido 1002", "records"))
    assert out.results[0].status == "error" and out.replans == ["records:exception:RuntimeError->escalation"]


def test_supervisor_respects_max_steps(monkeypatch):
    calls = []

    def always_insufficient(c):
        calls.append(1)
        return AgentResult("faq", "insufficient", reason="x")

    for name in ("faq", "records", "escalation"):
        monkeypatch.setitem(supervisor.AGENTS, name, always_insufficient)
    out = supervisor.run(["faq", "records"], ctx("x"))
    assert len(out.route) <= supervisor.MAX_STEPS and len(calls) <= supervisor.MAX_STEPS


def test_low_confidence_adds_escalation():
    out = supervisor.run(["faq"], ctx("Qual o prazo de entrega?"), confidence=0.3)
    assert out.route[-1] == "escalation" and out.replans[0].startswith("router:low_confidence")


# --- LLM (cliente falso: sem rede, sem chave) ----------------------------------------------------
def test_roles_have_explicit_models_and_effort(monkeypatch):
    assert (model_for("supervisor"), effort_for("supervisor")) == ("claude-sonnet-5-5", "low")
    assert (model_for("router"), effort_for("router")) == ("claude-haiku-5-5", "low")
    assert (model_for("agent"), effort_for("agent")) == ("claude-sonnet-5-5", "medium")
    assert (model_for("judge"), effort_for("judge")) == ("claude-opus-5-5", "medium")
    monkeypatch.setenv("SUPERVISOR_EFFORT", "medium")
    assert effort_for("supervisor") == "medium"


def test_llm_reads_insufficient_and_replans_to_escalation(monkeypatch):
    new_trace_id()
    llm, msgs = fake_llm([
        resp("tool_use", tool_use("faq_agent", {"query": "capital da Mongólia"})),
        resp("tool_use", tool_use("escalation_agent", {"reason": "sem fonte"}, "tu2")),
        resp("end_turn", text("Encaminhei para um atendente.")),
    ])
    use_llm(monkeypatch, llm)
    monkeypatch.setitem(supervisor.AGENTS, "faq", lambda c: AgentResult("faq", "insufficient", reason="no_source"))
    out = supervisor.run(["faq"], ctx("Qual a capital da Mongólia?"))
    assert out.engine == "llm" and out.route == ["faq", "escalation"]
    assert out.replans == ["faq->escalation (plano: [])"] and out.answer == "Encaminhei para um atendente."
    assert out.results[-1].handoff.reason == "faq_no_source"  # motivo veio do status lido
    seen = json.loads(msgs.sent[1]["messages"][-1]["content"][0]["content"])
    assert seen["status"] == "insufficient"  # o modelo enxergou o status antes de decidir
    first = msgs.sent[0]
    assert first["model"] == "claude-sonnet-5-5" and first["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k", "tool_choice"} & first.keys()  # nada forçado, nada de sampling
    assert request_usage()["cost_usd"] > 0


def test_llm_reads_error_and_escalates(monkeypatch):
    llm, msgs = fake_llm([
        resp("tool_use", tool_use("records_agent", {"request": "pedido 1002"})),
        resp("tool_use", tool_use("escalation_agent", {"reason": "ferramenta falhou"}, "tu2")),
        resp("end_turn", text("Abri um caso.")),
    ])
    use_llm(monkeypatch, llm)
    monkeypatch.setitem(supervisor.AGENTS, "records", lambda c: AgentResult("records", "error", reason="tool_error"))
    out = supervisor.run(["records"], ctx("Onde está o pedido 1002?", "records"))
    assert out.route == ["records", "escalation"] and out.results[-1].handoff.reason == "records_tool_error"
    assert json.loads(msgs.sent[1]["messages"][-1]["content"][0]["content"])["status"] == "error"


def test_llm_disallowed_transition_is_refused(monkeypatch):
    llm, msgs = fake_llm([
        resp("tool_use", tool_use("escalation_agent", {"reason": "pedido"})),
        resp("tool_use", tool_use("records_agent", {"request": "y"}, "tu2")),
        resp("end_turn", text("ok")),
    ])
    use_llm(monkeypatch, llm)
    out = supervisor.run(["escalation"], ctx("atendente", "human_request"))
    assert out.route == ["escalation"]  # escalation é terminal
    refused = msgs.sent[2]["messages"][-1]["content"][0]
    assert refused["is_error"] is True and "não permitida" in refused["content"]


def test_llm_failure_falls_back_to_table(monkeypatch):
    llm, _ = fake_llm([LLMError("api_status_500", "api_status")])
    use_llm(monkeypatch, llm)
    out = supervisor.run(["faq"], ctx("Qual o prazo de entrega?"))
    assert out.engine == "table" and out.route == ["faq"]


def test_llm_sub_questions_reach_each_parallel_agent(monkeypatch):
    llm, _ = fake_llm([
        resp("tool_use", tool_use("faq_agent", {"query": "prazo de entrega"}, "tu1"),
             tool_use("records_agent", {"request": "status do pedido 1002"}, "tu2")),
        resp("end_turn", text("Prazo de 3 a 5 dias; pedido em separação.")),
    ])
    use_llm(monkeypatch, llm)
    seen = {}

    def spy(name):
        def agent(c):
            seen[name] = c.facts.get("ask", {}).get(name)
            return AgentResult(name, "ok", text=name)
        return agent

    monkeypatch.setitem(supervisor.AGENTS, "faq", spy("faq"))
    monkeypatch.setitem(supervisor.AGENTS, "records", spy("records"))
    out = supervisor.run(["faq", "records"], ctx("Qual o prazo? E onde está meu pedido 1002?", "multi"))
    assert seen == {"faq": "prazo de entrega", "records": "status do pedido 1002"}
    assert out.route == ["faq", "records"] and out.replans == []


def test_agent_span_logs_status_and_reason(monkeypatch, caplog):
    # sem isso o log do smoke de 31/10 não dizia qual agente desistiu nem por quê
    monkeypatch.setitem(supervisor.AGENTS, "faq",
                        lambda c: AgentResult("faq", "insufficient", reason="not_in_sources"))
    with caplog.at_level("INFO"):
        supervisor.run_batch(["faq"], ctx("x"))
    spans = [r.extra_fields for r in caplog.records if getattr(r, "extra_fields", {}).get("span") == "agent"]
    assert spans and spans[-1]["status"] == "insufficient" and spans[-1]["reason"] == "not_in_sources"


def test_replan_ablation_keeps_the_router_plan(monkeypatch):
    # eval --ablate-replan: sem desvio, a falha do records NÃO vira escalation; com, vira (mede o valor do replan)
    monkeypatch.setitem(supervisor.AGENTS, "records", lambda c: AgentResult("records", "error", reason="tool_error"))
    monkeypatch.setenv("SUPERVISOR_REPLAN", "off")
    off = supervisor.run(["records"], ctx("pedido 1002", "records"))
    assert off.route == ["records"] and off.replans == []
    monkeypatch.setenv("SUPERVISOR_REPLAN", "on")
    on = supervisor.run(["records"], ctx("pedido 1002", "records"))
    assert on.route == ["records", "escalation"] and on.replans


def test_ablation_eval_writes_its_own_file_and_readme_shows_the_gain(tmp_path, monkeypatch):
    import json

    from evals import run_evals
    from scripts.fill_readme import replan_block

    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    run_evals.run(out=tmp_path / "latest.json", split="replan")
    monkeypatch.setenv("SUPERVISOR_REPLAN", "off")
    run_evals.run(out=tmp_path / "ablation_replan.json", split="replan")
    block = replan_block(tmp_path)
    assert "offline SEM replanejamento" in block and "Ablação (offline)" in block
    assert json.loads((tmp_path / "ablation_replan.json").read_text("utf-8"))["metrics"]["replan"]["cases"] >= 1
