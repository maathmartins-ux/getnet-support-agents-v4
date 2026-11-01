"""Item 1 — pergunta composta: divisão em ":", conjunções e "duas perguntas"; conversa não vira intenção.

Origem: held-out cego da v3 (31/10/2026). "Me diz quanto eu vendi ontem e aproveita: o Link ... ?" e
"Quick two questions: ..., and ...?" viraram uma intenção só; "Oi! Hoje tá frio, né? ... vendi ontem?" virou
multi porque a cláusula de conversa contava como FAQ. Aqui no vocabulário do kit (pedido / prazo / troca).
"""
import pytest

from app.router import classify_offline


@pytest.mark.parametrize("text_", [
    "Tenho uma dúvida sobre o prazo de entrega: meu pedido 1002 já saiu?",          # dois-pontos
    "What is the delivery time for capitals, and where is my order 1002?",         # ", and"
    "Onde está meu pedido 1002 e aproveita, qual o prazo de troca?",               # " e aproveita"
    "Quero saber o status do pedido 1002 e também como funciona a troca.",         # " e também"
    "Where is my order 1002 also how does the return policy work?",                # "also"
    "Duas perguntas: como funciona a troca e onde está meu pedido 1002?",          # anúncio + " e "
    "Quick two questions: how does the return policy work and where is my order 1002?",
])
def test_compound_question_is_multi(text_):
    d = classify_offline(text_, [])
    assert (d.intent, d.plan) == ("multi", ["faq", "records"])


@pytest.mark.parametrize("text_,intent", [
    ("Hoje tá frio, né? Onde está meu pedido 1002?", "records"),
    ("Oi! Hoje tá frio, né? Enfim, onde está meu pedido 1002?", "records"),
    ("What a rainy day, right? Where is my order 1002?", "records"),
    ("Bom dia! Onde está meu pedido 1002?", "records"),  # "dia" está na base ("dias úteis"), mas é conversa
    ("Nossa, que semana corrida. Qual o prazo de entrega?", "faq"),
])
def test_small_talk_clause_does_not_add_intent(text_, intent):
    assert classify_offline(text_, []).intent == intent


def test_small_talk_alone_keeps_default():
    # sem vocabulário do domínio em nenhuma cláusula: segue o padrão de sempre (faq), não some
    assert classify_offline("Hoje tá frio, né?", []).intent == "faq"


@pytest.mark.parametrize("text_,intent", [
    ("Qual o prazo de troca e devolução?", "faq"),       # " e " sem anúncio de duas perguntas não divide
    ("Cancel order 1002 and refund me", "records"),
    ("Quero falar com um atendente: meu pedido 1002 sumiu.", "human_request"),
])
def test_split_does_not_break_single_questions(text_, intent):
    assert classify_offline(text_, []).intent == intent
