"""Checagem numérica determinística: todo número da resposta precisa estar na evidência ou na pergunta.

Não depende de juiz LLM: um preço inventado é sinalizado mesmo quando o juiz gosta da resposta. Aponta números
a conferir; não afirma que estão errados (uma soma correta também aparece). Ideia vista em entrega pública do
case (31/10/2026, ver references/fde-excelencia.md §10); implementação própria.
"""
from fastapi.testclient import TestClient

from app import pipeline, supervisor
from app.agents import AgentResult
from app.api import app
from app.auth import issue_token
from app.grounding import numbers, unsupported_numbers
from app.observability import METRICS


def test_formats_normalize_to_the_same_number():
    assert numbers("R$ 79,90") == numbers("79.9") == {"79.9"}
    assert numbers("R$ 1.234,56") == numbers("1234.56") == {"1234.56"}
    assert numbers("taxa de 2,5%") == {"2.5"} and numbers("das 9h às 18h") == {"9", "18"}


def test_markers_and_ids_are_not_numbers():
    assert numbers("Capitais: 3 a 5 dias [1]. Interior [2].") == {"3", "5"}
    assert numbers("1. Abra o app\n2) Toque em pedidos") == set()
    assert numbers("protocolo T-f70e7d6e, ação a1b2c3") == set()


def test_invented_value_is_flagged_and_supported_ones_are_not():
    evidence = ["Frete: R$ 59,90 para capitais, 3 a 5 dias úteis."]
    assert unsupported_numbers("O frete custa R$ 59,90 e chega em 3 a 5 dias.", evidence) == []
    assert unsupported_numbers("O frete custa R$ 49,90 e chega em 2 dias.", evidence) == ["49,90", "2"]


def test_numbers_from_the_question_count_as_evidence():
    assert unsupported_numbers("Pedido 1002: em separação.", [], question="Onde está o pedido 1002?") == []


def test_offline_answers_are_fully_grounded():
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {issue_token('u1')}"}
    for message in ("Qual o prazo de entrega?", "Onde está meu pedido 1002?", "Qual o horário de atendimento?"):
        body = client.post("/chat", json={"message": message}, headers=auth).json()
        assert body["unsupported_numbers"] == [], (message, body["answer"])


def test_pipeline_flags_invented_number_and_counts_it(monkeypatch):
    def liar(ctx):
        return AgentResult("faq", "ok", text="O frete custa R$ 49,90.", evidence=["Frete: R$ 59,90."])

    monkeypatch.setitem(supervisor.AGENTS, "faq", liar)
    before = METRICS.counters.get(("numeric_ungrounded_total", ()), 0)
    from app.schemas import ChatRequest

    resp = pipeline.handle(ChatRequest(message="Quanto custa o frete?"), "u1")
    assert resp.unsupported_numbers == ["49,90"]
    assert METRICS.counters.get(("numeric_ungrounded_total", ()), 0) == before + 1


def test_financial_promise_without_source_is_flagged():
    from app.grounding import risk_flags

    assert risk_flags("Fique tranquilo, garantimos que o dinheiro cai hoje.", ["agenda: crédito D+30"]) == [
        "promessa_financeira"]
    assert risk_flags("We guarantee a full refund.", []) == ["promessa_financeira", "compromisso_legal"]


def test_promise_backed_by_the_source_is_not_flagged():
    from app.grounding import risk_flags

    source = "Pix Garantido: o valor é garantido pelo banco emissor."
    assert risk_flags("O valor é garantido pelo banco emissor (fonte [1]).", [source]) == []
    assert risk_flags("O pedido 1002 foi enviado.", ["pedido 1002 enviado"]) == []


def test_scale_glued_to_number_is_supported():
    """Getnet v4 (01/11/2026): o site escreve "R$ 80mil"; a resposta "R$ 80 mil" era acusada como número sem base."""
    from app.grounding import unsupported_numbers

    assert unsupported_numbers("Free above R$ 80 mil a month", ["Acima de R$ 80mil por mês"]) == []
    assert unsupported_numbers("Free above R$ 81 mil a month", ["Acima de R$ 80mil por mês"]) == ["81"]
