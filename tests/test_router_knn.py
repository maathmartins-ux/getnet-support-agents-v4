"""Item 3 — generalização offline: k-NN de TF-IDF (n-gramas de caractere 3-5) quando nenhuma regra casa.

Origem: v3 (31/10/2026). O roteador offline era regex sobre texto literal: 50% no held-out cego, 41,7% no hard.
Paráfrase sem a palavra da regra ("my approved sales", "minha maquininha não liga") virava a intenção padrão.
"""
import json

import pytest

from app import router
from app.router import KNN, classify_offline, knn_intent, normalize


def test_normalize_lowercases_and_strips_accents():
    assert normalize("Minha Maquininha NÃO liga!  Já?") == "minha maquininha nao liga ja"


@pytest.mark.parametrize("text_,intent", [
    ("minhas compras já chegaram?", "records"),               # nenhuma regra: não diz pedido/order/número
    ("show me my approved purchases", "records"),
    ("quero conversar com alguém da equipe", "human_request"),  # "conversar", não "falar": a regra não casa
    ("how long does delivery take?", "faq"),
])
def test_unseen_paraphrase_goes_to_right_intent(text_, intent):
    assert knn_intent(text_) == intent
    assert classify_offline(text_, []).intent == intent


@pytest.mark.parametrize("text_", ["Hoje tá frio, né?", "xyz qwerty", "Quem ganhou o jogo de futebol?"])
def test_below_threshold_keeps_default(text_):
    assert knn_intent(text_) is None
    assert classify_offline(text_, []).intent == "faq"  # padrão de sempre, sem inventar intenção


def write_examples(tmp_path, rows):
    (tmp_path / router.EXAMPLES_FILE).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_generalizes_on_domain_examples(tmp_path, monkeypatch):
    # Os exemplos do enunciado do item, com 4 exemplos por intenção (formato que o domínio preenche no relógio).
    write_examples(tmp_path, [
        {"text": "a maquininha está desligada", "intent": "records"},
        {"text": "minha máquina não acende", "intent": "records"},
        {"text": "my card machine won't turn on", "intent": "records"},
        {"text": "list my sales from yesterday", "intent": "records"},
        {"text": "show my approved transactions", "intent": "records"},
        {"text": "como funciona o link de pagamento?", "intent": "faq"},
        {"text": "quais as taxas do crédito parcelado?", "intent": "faq"},
        {"text": "what are the fees for debit?", "intent": "faq"},
        {"text": "how does the payment link work?", "intent": "faq"},
        {"text": "quero conversar com uma pessoa", "intent": "human_request"},
        {"text": "let me speak with a person", "intent": "human_request"},
        {"text": "me transfere para alguém da equipe", "intent": "human_request"},
    ])
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert knn_intent("minha maquininha não liga") == "records"
    assert knn_intent("my approved sales") == "records"
    assert knn_intent("qual a taxa do débito?") == "faq"


def test_no_examples_file_means_no_knn(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert knn_intent("minhas compras já chegaram?") is None
    assert classify_offline("minhas compras já chegaram?", []).intent == "faq"


def test_knn_is_deterministic_and_weighted():
    knn = KNN([("status da compra", "records"), ("status do pedido", "records"), ("status da entrega", "faq"),
               ("prazo da entrega", "faq"), ("prazo de troca", "faq")])
    first = knn.neighbors("status da minha compra", 5)
    assert first == knn.neighbors("status da minha compra", 5)
    assert [s for s, _ in first] == sorted((s for s, _ in first), reverse=True)
    # 3 vizinhos faq contra 2 records, mas os records são bem mais parecidos: a soma ponderada decide
    assert knn.predict("status da minha compra") == "records"


def test_examples_with_unknown_intent_are_ignored(tmp_path, monkeypatch):
    write_examples(tmp_path, [{"text": "minhas compras", "intent": "inventada"},
                              {"text": "status das minhas compras", "intent": "records"}])
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert knn_intent("minhas compras chegaram") == "records"
