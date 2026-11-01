"""Item 2 — seguimento de conversa: última intenção COM ASSUNTO e a conversa inteira chegando ao roteador.

Origem: v3 (31/10/2026). memory-04: "Minha maquininha não conecta." → "Ok, vou tentar reiniciar o roteador." →
"Ela ainda está sem sinal?" perdia o fio porque o roteador olhava só a intenção da ÚLTIMA fala. E num caso de
26 mensagens o primeiro turno sumiu: o roteador recebia só HISTORY_WINDOW=20 mensagens.
"""
import pytest

from app import pipeline
from app.router import classify_offline
from app.schemas import ChatRequest


def turn(content, intent):
    return [{"role": "user", "content": content, "intent": intent},
            {"role": "assistant", "content": "resposta", "intent": intent}]


@pytest.mark.parametrize("ack", [
    "Ok, vou tentar de novo amanhã.",
    "Ok, thanks.",
    "Beleza, obrigado!",
    "hmm",
])
def test_follow_up_skips_acknowledgement(ack):
    history = turn("Onde está meu pedido 1002?", "records") + turn(ack, "faq")
    assert classify_offline("E ele já saiu?", history).intent == "records"


def test_follow_up_uses_last_turn_with_subject():
    # a última fala COM assunto é de FAQ: "e isso?" não volta para o pedido de três turnos atrás
    history = (turn("Onde está meu pedido 1002?", "records") + turn("Ok.", "faq")
               + turn("Qual o prazo de entrega?", "faq"))
    assert classify_offline("E isso?", history).intent == "faq"


def chat(message, conv=None, user="u1"):
    return pipeline.handle(ChatRequest(message=message, conversation_id=conv), user)


def test_router_sees_whole_conversation(monkeypatch):
    seen = []
    real_route = pipeline.route
    monkeypatch.setattr(pipeline, "route", lambda text, history: seen.append(history) or real_route(text, history))
    conv = chat("Onde está meu pedido 1002?").conversation_id
    for _ in range(12):  # 1 + 12 turnos = 26 mensagens: o 1º sai da janela de 20
        chat("Ok, entendi.", conv)
    out = chat("E ele já saiu?", conv)
    assert any(m["role"] == "user" and m["intent"] == "records" and "1002" in m["content"] for m in seen[-1])
    assert out.intent == "records"
    # sem estourar o contexto: falas antigas chegam cortadas e só as do cliente
    older = seen[-1][:-pipeline.HISTORY_WINDOW]
    assert older and all(m["role"] == "user" and len(m["content"]) <= pipeline.ROUTER_OLDER_CHARS for m in older)
