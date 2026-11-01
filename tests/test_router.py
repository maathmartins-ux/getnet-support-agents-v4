"""Item 3 — router: Haiku com JSON schema quando há LLM; regra determinística offline ou em erro."""
import json

import pytest
from fakes import fake_llm, resp, text

from app import router
from app.llm import LLMError
from app.router import classify_offline, detect_language, route


@pytest.mark.parametrize("text_,lang", [
    ("I want to talk to a human agent please", "en"),
    ("Where is my order 1002?", "en"),
    ("Can I return a product?", "en"),
    ("Olá, como funciona a troca?", "pt"),
    ("Quero falar com um atendente", "pt"),
    ("Onde está meu pedido 1002?", "pt"),
])
def test_detect_language(text_, lang):
    assert detect_language(text_) == lang


@pytest.mark.parametrize("text_,intent,plan", [
    ("Quero falar com um atendente", "human_request", ["escalation"]),
    ("Onde está meu pedido 1002?", "records", ["records"]),
    ("Qual o prazo de entrega?", "faq", ["faq"]),
    ("Qual o prazo de entrega? E onde está meu pedido 1002?", "multi", ["faq", "records"]),
    ("Olá! Onde está meu pedido 1002?", "records", ["records"]),  # saudação não vira pergunta de FAQ
    ("Meu pedido 1002 atrasou. Quero falar com um atendente.", "human_request", ["escalation"]),
])
def test_offline_router(text_, intent, plan):
    d = classify_offline(text_, [])
    assert (d.intent, d.plan) == (intent, plan)


def test_follow_up_needs_history():
    assert classify_offline("e isso?", []).intent == "faq"
    history = [{"role": "user", "content": "pedido 1002", "intent": "records"}]
    assert classify_offline("e isso?", history).intent == "records"


def use(monkeypatch, llm):
    monkeypatch.setattr(router, "get_llm", lambda role="agent": llm)


def test_llm_decision_is_used_with_json_schema(monkeypatch):
    decision = {"intent": "multi", "plan": ["faq", "records"], "language": "pt", "confidence": 1.7}
    llm, msgs = fake_llm([resp("end_turn", text(json.dumps(decision)))], model="claude-haiku-5-5", effort="low")
    use(monkeypatch, llm)
    d = route("prazo e status do pedido 1002 numa frase só", [])
    assert (d.intent, d.plan, d.confidence) == ("multi", ["faq", "records"], 1.0)  # confiança limitada a 1
    sent = msgs.sent[0]
    assert sent["model"] == "claude-haiku-5-5" and sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["schema"] == router.SCHEMA


@pytest.mark.parametrize("script", [
    [resp("end_turn", text("não é json"))],
    [resp("end_turn", text(json.dumps({"intent": "inventada", "plan": [], "language": "pt", "confidence": 1})))],
    [resp("refusal", text(""))],
    [LLMError("api_status_529", "api_status")],
])
def test_llm_failure_falls_back_to_rules(monkeypatch, script):
    llm, _ = fake_llm(script, model="claude-haiku-5-5", effort="low")
    use(monkeypatch, llm)
    assert route("Quero falar com um atendente", []).intent == "human_request"


def test_clear_language_overrides_only_unambiguous_sentences():
    """Getnet v4 ao vivo: termo pt entre parênteses numa frase inglesa não pode virar resposta em português."""
    from app.router import clear_language

    assert clear_language("How does receivables advance (antecipação) work with Getnet?") == "en"
    assert clear_language("Como funciona a antecipação das minhas vendas na Getnet?") == "pt"
    assert clear_language("Get Smart?") is None
