"""Frente R — supervisor em tabela com o domínio Getnet: replanejamento dentro do ALLOWED e ordem do DEPENDS."""
from app import supervisor
from app.agents import AgentResult, Context


def ctx(text, intent):
    return Context(text=text, language="pt", user_id="cliente1988", conversation_id="c-r", intent=intent)


def fake(agent, status, reason=None, calls=None):
    def run(c):
        if calls is not None:
            calls.append(agent)
        return AgentResult(agent, status, reason=reason)
    return run


def test_records_error_goes_to_knowledge_then_human(domain_data, monkeypatch):
    monkeypatch.setitem(supervisor.AGENTS, "records", fake("records", "error", "tool_error"))
    monkeypatch.setitem(supervisor.AGENTS, "faq", fake("faq", "insufficient", "no_source"))
    out = supervisor.run_table(["records"], ctx("Quando cai o dinheiro de ontem?", "support"))
    assert out.route == ["records", "faq", "escalation"]
    assert out.replans == ["records:tool_error->faq", "faq:no_source->escalation"]


def test_knowledge_without_source_escalates(domain_data, monkeypatch):
    monkeypatch.setitem(supervisor.AGENTS, "faq", fake("faq", "insufficient", "no_source"))
    out = supervisor.run_table(["faq"], ctx("A Getnet vende painel solar?", "knowledge"))
    assert out.route == ["faq", "escalation"] and out.replans == ["faq:no_source->escalation"]


def test_general_without_source_never_escalates(domain_data, monkeypatch):
    monkeypatch.setitem(supervisor.AGENTS, "faq", fake("faq", "insufficient", "no_source"))
    out = supervisor.run_table(["faq"], ctx("Vai chover amanhã em Porto Alegre?", "general"))
    assert out.route == ["faq"] and "escalation" not in out.route and out.replans == []
    low = supervisor.run_table(["faq"], ctx("Cotação do euro?", "general"), confidence=0.1)
    assert "escalation" not in low.route  # nem a confiança baixa abre caso humano para general


def test_troubleshoot_runs_records_before_faq(domain_data, monkeypatch):
    calls = []
    monkeypatch.setitem(supervisor.AGENTS, "records", fake("records", "ok", calls=calls))
    monkeypatch.setitem(supervisor.AGENTS, "faq", fake("faq", "ok", calls=calls))
    assert supervisor.next_batch(["records", "faq"], 4) == ["records"]  # DEPENDS: faq lê o estado do terminal
    out = supervisor.run_table(["records", "faq"], ctx("Minha maquininha não conecta", "troubleshoot"))
    assert calls == ["records", "faq"] and out.route == ["records", "faq"] and out.replans == []


def test_multi_runs_in_parallel_batch(domain_data):
    # multi não depende: faq e records no mesmo lote (só faq depende de records quando records vem antes)
    assert supervisor.next_batch(["records", "faq"], 4) == ["records"]
    assert supervisor.next_batch(["faq", "records"], 4) == ["faq", "records"]
