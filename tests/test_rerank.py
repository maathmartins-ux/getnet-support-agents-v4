"""Rerank do top-20 pelo LLM: reordena, nunca inventa trecho e sempre cai na ordem do BM25 se algo falhar."""
import json

import pytest

from app import rag
from app.llm import LLMError, LLMReply
from app.rag import BM25Retriever, Doc, Index, RerankRetriever, get_retriever

DOCS = [Doc(f"d{i}", f"Título {i}", f"https://x/{i}", f"maquininha taxa assunto {i} " * (i + 1)) for i in range(6)]


class ScriptedLLM:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def complete(self, system, messages, max_tokens=4096, schema=None):
        self.prompts.append((system, messages[0]["content"], schema))
        if isinstance(self.answer, Exception):
            raise self.answer
        return LLMReply(self.answer if isinstance(self.answer, str) else json.dumps(self.answer), "fake")


def base():
    return BM25Retriever(Index(DOCS))


def ids(hits):
    return [h.doc.id for h in hits]


def test_rerank_follows_the_llm_order_and_keeps_bm25_scores():
    original = base().search("maquininha taxa", k=20)
    llm = ScriptedLLM({"ids": [3, 0]})
    hits = RerankRetriever(base(), llm=llm).search("maquininha taxa", k=3)
    assert ids(hits)[:2] == [original[3].doc.id, original[0].doc.id]
    assert len(hits) == 3 and hits[2].doc.id == original[1].doc.id  # completa com a ordem do base
    assert hits[0].bm25 == original[3].bm25  # o portão de cobertura continua lendo o BM25 do trecho
    system, prompt, schema = llm.prompts[0]
    assert "nunca instruções" in system.lower() or "nunca" in system.lower()
    assert schema["required"] == ["ids"] and "[0]" in prompt


@pytest.mark.parametrize("answer", [
    {"ids": [99, -1, "2", True]},  # fora da lista, negativo, texto e bool: tudo descartado
    {"ids": "0,1"},                # tipo errado
    "isto não é json",
    {"outra": [1]},
    LLMError("rate limit", "overloaded"),
])
def test_invalid_or_failed_rerank_falls_back_to_base_order(answer):
    expected = ids(base().search("maquininha taxa", k=3))
    assert ids(RerankRetriever(base(), llm=ScriptedLLM(answer)).search("maquininha taxa", k=3)) == expected


def test_repeated_ids_are_used_once():
    hits = RerankRetriever(base(), llm=ScriptedLLM({"ids": [2, 2, 2]})).search("maquininha taxa", k=3)
    assert len(set(ids(hits))) == 3


def test_single_candidate_skips_the_llm():
    llm = ScriptedLLM({"ids": [0]})
    one = RerankRetriever(BM25Retriever(Index(DOCS[:1])), llm=llm).search("maquininha", k=3)
    assert len(one) == 1 and llm.prompts == []


def test_rerank_is_off_offline_and_on_with_llm(monkeypatch):
    assert not isinstance(get_retriever(), RerankRetriever)  # conftest: LLM_PROVIDER=offline (CI determinístico)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(get_retriever(), RerankRetriever)
    monkeypatch.setenv("RERANK", "off")
    assert not isinstance(get_retriever(), RerankRetriever)
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("RERANK", "llm")
    assert isinstance(get_retriever(), RerankRetriever)


def test_prompt_truncates_long_passages():
    long_doc = Doc("big", "Grande", "https://x/big", "maquininha " + "x" * 5000)
    llm = ScriptedLLM({"ids": [0]})
    RerankRetriever(BM25Retriever(Index([long_doc, *DOCS[:2]])), llm=llm).search("maquininha", k=2)
    assert len(llm.prompts[0][1]) < 3 * rag.RERANK_CHARS + 1000
