"""O eval mede o retriever base; o rerank entra só como linha própria do comparativo.

Achado da integração do kit 3.5.0 (31/10/2026): com LLM_PROVIDER=anthropic, calibrate_gate e retrieval_metrics
chamavam o Haiku em cada pergunta, fora do teto --max-usd, e o limiar do portão era calibrado sobre outra ordem.
"""
import json

import pytest

from app import rag
from app.llm import LLMReply
from evals.run_evals import calibrate_gate, compare_retrievers, retrieval_metrics


@pytest.fixture
def llm_on_but_rerank_forbidden(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    def boom(self, query, k=3):
        raise AssertionError("o eval chamou o rerank sem pedir")

    monkeypatch.setattr(rag.RerankRetriever, "search", boom)


def test_retrieval_metrics_measure_the_base_retriever(llm_on_but_rerank_forbidden):
    assert retrieval_metrics()["recall@3"] >= 0


def test_gate_calibration_uses_the_base_retriever(llm_on_but_rerank_forbidden):
    assert calibrate_gate()["best"] is not None


class FirstLast:
    """Rerank falso: inverte a ordem (prova que a linha nova é medida com o rerank, não com o base)."""

    def complete(self, system, messages, max_tokens=4096, schema=None):
        n = messages[0]["content"].count("\n[")
        return LLMReply(json.dumps({"ids": list(range(n, -1, -1))}), "fake")


def test_compare_adds_a_rerank_row_only_when_asked():
    plain = compare_retrievers()
    assert "bm25+rerank" not in plain
    out = compare_retrievers(rerank_llm=FirstLast())
    assert out["bm25+rerank"]["retriever"] == "bm25+rerank"
    assert out["bm25+rerank"]["mrr"] != out["bm25"]["mrr"]  # ordem invertida muda o MRR no exemplo do kit
