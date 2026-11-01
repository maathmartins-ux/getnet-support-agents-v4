"""Falsos positivos da checagem de números vistos no eval ao vivo da Getnet v3 (31/10/2026).

Casos reais de evals/results/live_approval.json da v3:
- approval-01: "Venda de 30/10/2026" com a evidência da tool em ISO ("2026-10-30") → marcou 30, 10 e 2026;
- resposta em inglês "R$ 5,000.00" com a evidência 5000.0 → "5,000.00" virava 5 (vírgula lida como decimal).
"""
import json

from app.grounding import numbers, unsupported_numbers

TOOL_RESULT = json.dumps({"ok": True, "data": {"sale_date": "2026-10-30", "total": 5000.0,
                                               "items": [{"amount": 1200.0, "deposit_date": "2026-11-03"}]}})


def test_br_date_in_answer_matches_iso_date_in_evidence():
    answer = "Venda de 30/10/2026: R$ 5.000,00, com depósito previsto para 03/11/2026."
    assert unsupported_numbers(answer, [TOOL_RESULT]) == []


def test_iso_date_in_answer_and_day_month_without_year_also_match():
    assert unsupported_numbers("Sale on 2026-10-30, deposit on 03/11.", [TOOL_RESULT]) == []
    assert unsupported_numbers("Deposit tomorrow (Oct 30).", [TOOL_RESULT]) == []  # dia citado sozinho


def test_us_date_order_is_accepted_when_unambiguous():
    assert unsupported_numbers("Sale on 10/30/2026.", [TOOL_RESULT]) == []


def test_invented_date_is_still_flagged_as_one_token():
    assert unsupported_numbers("Depósito em 05/11/2026.", [TOOL_RESULT]) == ["05/11/2026"]


def test_en_and_pt_money_formats_are_the_same_value():
    assert numbers("R$ 5,000.00") == numbers("R$ 5.000,00") == numbers("5000.0") == {"5000"}
    assert numbers("1,234.56") == numbers("1.234,56") == {"1234.56"}
    assert unsupported_numbers("Your total was R$ 5,000.00.", [TOOL_RESULT]) == []
    assert unsupported_numbers("Your total was R$ 5,000.", [TOOL_RESULT]) == []  # milhar en sem centavos


def test_en_format_still_catches_an_invented_value():
    assert unsupported_numbers("Your total was R$ 4,000.00.", [TOOL_RESULT]) == ["4,000.00"]


def test_value_in_words_is_not_a_false_positive():
    # por extenso não tem dígito: não há o que marcar; "5 mil" é 5000, não 5
    assert unsupported_numbers("Você vendeu cinco mil reais ontem.", [TOOL_RESULT]) == []
    assert unsupported_numbers("Você vendeu R$ 5 mil ontem.", [TOOL_RESULT]) == []
    assert unsupported_numbers("Você vendeu R$ 1,2 mil no débito.", [TOOL_RESULT]) == []
    assert unsupported_numbers("Você vendeu R$ 7 mil ontem.", [TOOL_RESULT]) == ["7"]
    assert unsupported_numbers("Limite de R$ 2 milhões.", ['{"limit": 2000000}']) == []  # não é 2 mil


def test_date_with_year_is_only_supported_whole():
    assert numbers("30/10/2026") >= {"2026-10-30"} and "2026-10-30" in numbers("2026-10-30")
    # 03 (de 2026-11-03), 10 e 2026 (de 2026-10-30) estão na evidência, mas a data 03/10/2026 não está
    assert unsupported_numbers("Depósito em 03/10/2026.", [TOOL_RESULT]) == ["03/10/2026"]
