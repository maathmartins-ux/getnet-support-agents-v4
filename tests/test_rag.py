"""Item 7 — busca BM25 e híbrida (BM25 + embeddings, RRF) atrás de RETRIEVER, com embedder falso."""
from fakes import ConceptEmbedder

from app import agents, rag
from app.agents import Context, faq_agent
from app.rag import BM25Retriever, HybridRetriever, get_index, get_retriever, recall_at_k, reciprocal_rank, rrf


def ids(hits):
    return [h.doc.id for h in hits]


def test_bm25_ranks_relevant_first_and_metrics():
    assert ids(BM25Retriever(get_index()).search("Posso trocar um livro?"))[0] == "faq-troca"
    assert recall_at_k(["a", "faq-troca"], ["faq-troca"], 3) == 1.0
    assert reciprocal_rank(["a", "faq-troca"], ["faq-troca"]) == 0.5 and reciprocal_rank(["a"], ["x"]) == 0.0


def test_rrf_rewards_agreement_between_lists():
    fused = rrf([["a", "b"], ["b", "c"]])
    assert max(fused, key=fused.get) == "b"  # 2º + 1º vence 1º isolado
    assert fused["a"] == 1 / 61


def test_hybrid_finds_synonym_that_bm25_misses():
    q = "Como peço reembolso?"
    assert BM25Retriever(get_index()).search(q) == []  # nenhum termo em comum
    hits = HybridRetriever(get_index(), ConceptEmbedder()).search(q)
    assert hits[0].doc.id == "faq-troca" and hits[0].cosine > 0.9 and hits[0].bm25 == 0


def test_hybrid_is_deterministic():
    r = HybridRetriever(get_index(), ConceptEmbedder())
    assert ids(r.search("prazo de entrega")) == ids(r.search("prazo de entrega"))


def test_flag_selects_retriever_and_degrades_without_fastembed(monkeypatch):
    assert get_retriever().name == "bm25"
    monkeypatch.setenv("RETRIEVER", "hybrid")
    assert get_retriever(embedder=ConceptEmbedder()).name == "hybrid"

    def no_extra(*a, **k):
        raise ImportError("No module named 'fastembed'")

    rag._hybrid_for.cache_clear()
    monkeypatch.setattr(rag, "FastEmbedEmbedder", no_extra)
    assert get_retriever().name == "bm25"  # sem o extra [hybrid]: cai no BM25, não quebra
    rag._hybrid_for.cache_clear()


def test_faq_agent_answers_synonym_with_hybrid(monkeypatch):
    c = Context(text="Como peço reembolso?", language="pt", user_id="u1", conversation_id="c1")
    assert faq_agent(c).status == "insufficient"  # BM25: sem fonte
    monkeypatch.setattr(agents, "get_retriever", lambda: HybridRetriever(get_index(), ConceptEmbedder()))
    r = faq_agent(c)
    assert r.status == "ok" and r.sources[0].id == "faq-troca"


class NeighborEmbedder:
    """Vizinhos com cosseno alto (0,6) mas longe do melhor (1,0): não podem virar fonte."""

    name = "fixed"

    def embed(self, texts):
        table = {"Prazo de entrega.": [1.0, 0.0], "Troca": [0.6, 0.8], "Horário": [0.55, 0.835]}
        return [next((v for k, v in table.items() if t.startswith(k)), [1.0, 0.0]) for t in texts]


def test_hybrid_drops_neighbors_far_from_the_best_match(monkeypatch):
    monkeypatch.setattr(agents, "get_retriever", lambda: HybridRetriever(get_index(), NeighborEmbedder()))
    r = faq_agent(Context(text="Qual o prazo de entrega?", language="pt", user_id="u1", conversation_id="c1"))
    assert [s.id for s in r.sources] == ["faq-entrega"]


def test_compare_retrievers_reports_both():
    from evals.run_evals import compare_retrievers

    cmp = compare_retrievers(embedder=ConceptEmbedder())
    assert cmp["bm25"]["retriever"] == "bm25" and cmp["hybrid"]["retriever"] == "hybrid"
    assert cmp["hybrid"]["recall@3"] >= cmp["bm25"]["recall@3"]  # no exemplo do kit, híbrida ≥ BM25


def test_gate_threshold_is_chosen_by_measurement(monkeypatch):
    # feedback v2 (31/10/2026): o portão léxico barrava só 1 de 5 perguntas sem fonte; o limiar era chute
    from evals.run_evals import calibrate_gate

    cal = calibrate_gate()
    assert cal["positives"] >= 6 and cal["negatives"] >= 3 and cal["current"] == 0.5
    best = cal["best"]
    # critério v3.5 (auditoria 31/10): mais respondidas entre os limiares que recusam ≥ 85% dos sem fonte, e no
    # empate o MENOR. O critério antigo (acurácia balanceada, empate para o maior) levou 1 negativo a fixar o limiar
    # em 14,938 e o handoff oficial a 50% na v3.
    ok = [r for r in cal["table"] if r["neg_refused"] >= 0.85]
    assert best["pos_answered"] == max(r["pos_answered"] for r in ok)
    assert best["threshold"] == min(r["threshold"] for r in ok if r["pos_answered"] == best["pos_answered"])
    current = next(r for r in cal["table"] if r["threshold"] == 0.5)
    assert best["neg_refused"] >= current["neg_refused"]  # no exemplo: 0,5 deixa passar 1 de 3 sem fonte


def test_gate_threshold_comes_from_env(monkeypatch):
    from app import agents
    from app.rag import get_retriever

    found = get_retriever().search("Qual o prazo de entrega?", k=3)
    assert agents.covered_hits(found)  # padrão 0,5: cobre
    monkeypatch.setenv("COVERAGE_MIN_SCORE", "99")
    assert agents.covered_hits(found) == []


def test_negatives_do_not_count_in_recall():
    from evals.run_evals import retrieval_metrics

    m = retrieval_metrics()
    assert m["cases"] == m["positives"] and m["negatives"] >= 3
