"""Itens 5 e 6: idioma medido no TEXTO da resposta e intervalo de confiança de Wilson em toda taxa.

Getnet v3 (31/10/2026): 4 de 89 respostas ao vivo saíram no idioma errado e language_acc mostrava 100%,
porque conferia o campo declarado `language`, não o texto.
"""
import json

import pytest

from evals import run_evals
from evals.run_evals import run, score


@pytest.mark.parametrize("text,lang", [
    ("Seu pedido está em separação e deve chegar em até 3 dias úteis.", "pt"),
    ("Your order is being prepared and should arrive within 3 business days.", "en"),
    ("The Get Clássica and the Get Smart differ in the connection: the Smart has Wi-Fi.", "en"),
    ("Não encontrei base para responder com segurança. Posso encaminhar para um atendente humano.", "pt"),
    ("I couldn't find a reliable basis to answer. I can hand this to a human agent.", "en"),
])
def test_detects_language_of_the_text(text, lang):
    assert run_evals.detect_text_language(text) == lang


@pytest.mark.parametrize("text", ["OK", "R$ 5.000,00", "", "Livro B"])
def test_inconclusive_text_returns_none(text):
    assert run_evals.detect_text_language(text) is None


def test_language_check_reads_the_answer_not_the_declared_field():
    case = {"expect": {"intent": "faq", "language": "en"}}
    got = {"intent": "faq", "language": "en", "answer": "O prazo de entrega é de 3 a 5 dias úteis para a capital."}
    assert score(case, got)["language"] is False
    got["answer"] = "Delivery takes 3 to 5 business days to the capital."
    assert score(case, got)["language"] is True


def test_rows_record_detected_and_declared_language(tmp_path):
    report = run(out=tmp_path / "latest.json", split="official")
    row = next(r for r in report["rows"] if r["id"] == "official-04")
    assert row["language_detected"] == "en" and row["language_declared"] == "en"


# --- Wilson ----------------------------------------------------------------------------------------
def test_wilson_known_values():
    lo, hi = run_evals.wilson(5, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.2366, 0.7634)
    lo, hi = run_evals.wilson(0, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.0, 0.2775)
    lo, hi = run_evals.wilson(10, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.7225, 1.0)
    lo, hi = run_evals.wilson(81, 263)  # exemplo clássico: 30,8% com n=263 → [25,5%; 36,6%]
    assert (round(lo, 3), round(hi, 3)) == (0.255, 0.366)
    assert run_evals.wilson(0, 0) is None


def test_every_rate_in_report_has_n_and_ci(tmp_path):
    report = run(out=tmp_path / "latest.json")
    saved = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    ci = saved["metrics"]["ALL"]["ci"]
    for key, value in saved["metrics"]["ALL"].items():
        if key.endswith("_acc") and value is not None:
            assert {"k", "n", "lo", "hi"} <= ci[key].keys(), key
            assert ci[key]["lo"] <= value <= ci[key]["hi"]
    assert saved["intent_ci"]["n"] == len([r for r in saved["rows"] if r["split"] not in run_evals.UNGATED])
    assert {"pass_at_1", "pass_hat_k"} <= saved["stability"]["ci"].keys()
    md = run_evals.to_markdown(report)
    assert "IC95" in md and "n=" in md
