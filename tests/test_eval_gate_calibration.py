"""Item 3: calibração do portão "sem fonte" (Getnet v3, 31/10/2026).

Acurácia balanceada com 14 negativos e empate para o limiar mais alto: um negativo com BM25 14,938 empurrou o
limiar para cima e o handoff oficial caiu para 50%. Critério novo: maximizar positivos respondidos com recusa
de negativos >= 85%; empate → o MENOR limiar. Negativo que o router não manda ao agente de conhecimento não
passa pelo portão e sai da conta.
"""
import json
from types import SimpleNamespace as NS

from evals import run_evals
from evals.run_evals import calibrate_gate, run

ROUTED_AWAY = ["Onde está meu pedido 1002?", "Quero falar com um atendente humano"]


class FakeRetriever:
    name = "fake"

    def __init__(self, table):
        self.table = table  # pergunta → [(doc_id, bm25)]

    def search(self, question, k=3):
        return [NS(doc=NS(id=d), bm25=s, cosine=None) for d, s in self.table[question]][:k]


def scenario(tmp_path):
    table, rows = {}, []
    for i in range(30):  # 28 com a fonte certa em 20 (e um trecho errado em 5); 2 com a fonte em 10
        q = f"pergunta positiva p{i:02d}"
        table[q] = [("doc", 20.0), ("outro", 5.0)] if i < 28 else [("doc", 10.0)]
        rows.append({"question": q, "relevant": ["doc"]})
    for i in range(14):  # 13 negativos fracos e 1 negativo forte (o 14,938 da v3)
        q = f"pergunta negativa n{i:02d}"
        table[q] = [("x", 14.938 if i == 13 else 1.0)]
        rows.append({"question": q, "relevant": []})
    for q in ROUTED_AWAY:  # o router manda para records/escalation: o portão do conhecimento nem roda
        table[q] = [("x", 30.0)]
        rows.append({"question": q, "relevant": []})
    path = tmp_path / "retrieval.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path, FakeRetriever(table)


def test_best_threshold_maximizes_answers_with_refusal_floor(tmp_path):
    path, retriever = scenario(tmp_path)
    cal = calibrate_gate(path, retriever)
    best = cal["best"]
    # 5,0 responde 100% dos com fonte e recusa 13/14 (92,9%); 20,0 (o critério antigo) só responde 93,3%
    assert best["threshold"] == 5.0, best
    assert best["pos_answered"] == 1.0 and best["neg_refused"] == 0.929
    assert cal["neg_refusal_min"] == 0.85 and cal["criterion"].startswith("max pos_answered")


def test_negatives_routed_elsewhere_leave_the_count(tmp_path):
    path, retriever = scenario(tmp_path)
    cal = calibrate_gate(path, retriever)
    assert cal["negatives"] == 14 and sorted(cal["excluded_negatives"]) == sorted(ROUTED_AWAY)


def test_current_is_the_effective_threshold_from_env(monkeypatch, tmp_path):
    path, retriever = scenario(tmp_path)
    monkeypatch.setenv("COVERAGE_MIN_SCORE", "14.938")
    cal = calibrate_gate(path, retriever)
    assert cal["current"] == 14.938 and cal["current_source"] == "COVERAGE_MIN_SCORE"
    assert cal["at_current"]["pos_answered"] == 0.933  # o que o limiar em uso entrega, medido
    monkeypatch.delenv("COVERAGE_MIN_SCORE")
    from app.agents import MIN_SCORE

    cal = calibrate_gate(path, retriever)
    assert cal["current"] == MIN_SCORE and cal["current_source"] == "MIN_SCORE"


def test_report_and_markdown_carry_the_effective_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("COVERAGE_MIN_SCORE", "0.7")
    report = run(out=tmp_path / "latest.json", split="official")
    saved = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert saved["coverage_min_score"] == {"effective": 0.7, "source": "COVERAGE_MIN_SCORE"}
    md = run_evals.to_markdown(report)
    assert "limiar efetivo 0.7 (COVERAGE_MIN_SCORE)" in md
