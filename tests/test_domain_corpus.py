"""Integridade do corpus COMMITADO do desafio (frente K). Pulado no kit puro (sem domínio do desafio)."""

import hashlib
import json

import pytest

from app.config import ROOT
from app.ingest import MAX_CHARS, MIN_CHARS, is_allowed, load_sources

CORPUS = ROOT / "data" / "knowledge"


def _corpus() -> list[dict]:
    return [json.loads(line) for f in sorted(CORPUS.rglob("*.jsonl")) for line in f.read_text("utf-8").splitlines()
            if line.strip()]


@pytest.fixture
def corpus(domain_data):
    docs = _corpus()
    if not docs:
        pytest.skip("corpus do desafio ainda não ingerido (frente K)")
    return docs


def test_corpus_is_literal_sized_hashed_and_from_allowed_hosts(corpus):
    hosts = load_sources(ROOT / "data" / "sources.json")["allowed_hosts"]
    ids = [d["id"] for d in corpus]
    assert len(ids) == len(set(ids)), "id duplicado no corpus"
    for d in corpus:
        assert is_allowed(d["url"], hosts), d["url"]
        assert MIN_CHARS <= len(d["text"]) <= MAX_CHARS, d["id"]
        assert d["sha256"] == hashlib.sha256(d["text"].encode("utf-8")).hexdigest(), d["id"]


def test_retrieval_questions_point_to_existing_ids(corpus):
    from app.jsonl import read_family

    ids = {d["id"] for d in corpus}
    rows = read_family(ROOT / "evals" / "retrieval.jsonl")
    assert rows, "sem perguntas de retrieval (evals/retrieval.d/)"
    missing = [(r["question"], sorted(set(r["relevant"]) - ids)) for r in rows if set(r["relevant"]) - ids]
    assert not missing, missing[:5]
