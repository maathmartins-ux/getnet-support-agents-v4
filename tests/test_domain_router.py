"""Frente R — router do domínio Getnet (offline): cenários oficiais, desempates da SPEC e paráfrases próprias."""
import json

import pytest

from app import router
from app.config import ROOT


def _official():
    rows = [json.loads(line) for line in (ROOT / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return [(r["message"], r["expect"]["intent"]) for r in rows if r["split"] == "official"]


OFFICIAL = _official()

# Paráfrases próprias (pt/en), nenhuma copiada dos cenários: exercitam os desempates da SPEC.
PARAPHRASES = [
    # sintoma → troubleshoot, mesmo com "minha maquininha"
    ("Minha maquininha não conecta no Wi-Fi, o que eu faço?", "troubleshoot"),
    ("The terminal froze in the middle of a sale", "troubleshoot"),
    ("Deu erro de transação negada na Get Smart", "troubleshoot"),
    ("Why was my customer's card rejected?", "troubleshoot"),
    # dado do lojista / ação dele → support
    ("Quando cai o dinheiro das vendas de ontem?", "support"),
    ("How much did I sell yesterday?", "support"),
    ("Qual é o meu saldo para receber?", "support"),
    ("Quero antecipar o meu valor disponível", "support"),
    ("Reinicie a maquininha T2024A", "support"),
    ("I want to contest transaction TX2001", "support"),
    ("Quais transações minhas foram recusadas hoje?", "support"),
    # produto sem pedir dado → knowledge (AC-04 e outro banco)
    ("Preciso ter conta bancária para receber por Pix?", "knowledge"),
    ("Do I need a bank account to get paid through the Payment Link?", "knowledge"),
    ("Como funciona a antecipação de recebíveis da Getnet?", "knowledge"),
    ("Posso parcelar no crediário em quantas vezes?", "knowledge"),
    ("A Getnet faz empréstimo para pessoa física?", "knowledge"),
    # fora da Getnet → general; câmbio NA Getnet → knowledge
    ("Vai chover em Florianópolis amanhã?", "general"),
    ("What is the dollar exchange rate right now?", "general"),
    ("A Getnet cobra taxa de câmbio em vendas com cartão estrangeiro?", "knowledge"),
    # humano
    ("Quero falar com um atendente humano", "human_request"),
    ("Can I talk to a real person?", "human_request"),
    # duas perguntas → multi; humano e troubleshoot vencem
    ("O que é o Get Tap? E qual o meu saldo?", "multi"),
    ("Explain the crediário, and also list my recent transactions.", "multi"),
    ("Minha maquininha travou. Quero falar com um atendente.", "human_request"),
    ("Quanto vendi hoje? A maquininha está dando erro.", "troubleshoot"),
]


@pytest.mark.parametrize("text,intent", OFFICIAL)
def test_official_scenarios(domain_data, text, intent):
    assert len(OFFICIAL) == 10
    assert router.classify_offline(text, []).intent == intent


@pytest.mark.parametrize("text,intent", PARAPHRASES)
def test_paraphrases(domain_data, text, intent):
    assert router.classify_offline(text, []).intent == intent


def test_plans_follow_spec(domain_data):
    d = router.domain.active()
    assert d.PLANS["troubleshoot"] == ["records", "faq"] and d.PLANS["general"] == ["faq"]
    assert router.classify_offline("What's the euro rate today?", []).plan == ["faq"]
    assert router.classify_offline("Meu terminal não liga", []).plan == ["records", "faq"]


def test_followup_inherits_subject(domain_data):
    history = [{"role": "user", "content": "Minha maquininha não conecta", "intent": "troubleshoot"},
               {"role": "assistant", "content": "Verifique o chip."}]
    assert router.classify_offline("E ela?", history).intent == "troubleshoot"
    history = [{"role": "user", "content": "How much did I sell yesterday?", "intent": "support"},
               {"role": "assistant", "content": "R$ 1.200."}]
    assert router.classify_offline("And that one?", history).intent == "support"


def test_small_talk_is_not_a_question(domain_data):
    d = router.domain.active()
    assert d.clause_intent("Olá!", [], router) is None
