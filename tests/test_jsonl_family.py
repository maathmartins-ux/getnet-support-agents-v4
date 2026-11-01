"""Arquivo por fork: `<nome>.jsonl` + `<nome>.d/*.jsonl` lidos juntos.

Kit 3.6.0: com 6+ forks, dois forks escrevendo no mesmo cases.jsonl dão conflito de merge e quebram a posse.
Cada fork escreve o próprio `evals/cases.d/<fork>.jsonl` (idem retrieval, calibração do juiz e exemplos do
roteador) e o runner lê a família inteira.
"""
import json

import pytest

from app import router
from app.jsonl import family, read_family
from evals import judge, run_evals


def _w(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_family_le_base_e_pasta_em_ordem(tmp_path):
    base = tmp_path / "cases.jsonl"
    _w(base, [{"n": 0}])
    _w(tmp_path / "cases.d" / "b.jsonl", [{"n": 2}])
    _w(tmp_path / "cases.d" / "a.jsonl", [{"n": 1}])
    (tmp_path / "cases.d" / "notas.txt").write_text("ignorado", encoding="utf-8")
    assert [r["n"] for r in read_family(base)] == [0, 1, 2]
    assert [p.name for p in family(base)] == ["cases.jsonl", "a.jsonl", "b.jsonl"]


def test_family_sem_base_le_so_a_pasta(tmp_path):
    _w(tmp_path / "x.d" / "a.jsonl", [{"n": 1}])
    assert read_family(tmp_path / "x.jsonl") == [{"n": 1}]


def test_family_vazia(tmp_path):
    assert family(tmp_path / "nada.jsonl") == []
    assert read_family(tmp_path / "nada.jsonl") == []


def _case(i, split="official"):
    return {"id": i, "split": split, "message": "m", "expect": {}}


def test_load_cases_junta_os_arquivos_dos_forks(tmp_path):
    base = tmp_path / "cases.jsonl"
    _w(base, [_case("o1")])
    _w(tmp_path / "cases.d" / "c1.jsonl", [_case("c1-1", "dev")])
    _w(tmp_path / "cases.d" / "e.jsonl", [_case("e-1", "guardrail")])
    ids = [c["id"] for c in run_evals.load_cases((base,))]
    assert ids == ["o1", "c1-1", "e-1"]


def test_load_cases_acusa_id_duplicado_entre_forks(tmp_path):
    base = tmp_path / "cases.jsonl"
    _w(base, [_case("x")])
    _w(tmp_path / "cases.d" / "c1.jsonl", [_case("x")])
    with pytest.raises(ValueError, match="duplicados"):
        run_evals.load_cases((base,))


def test_retrieval_le_a_familia(tmp_path, monkeypatch):
    base = tmp_path / "retrieval.jsonl"
    _w(tmp_path / "retrieval.d" / "a1.jsonl", [{"question": "q", "relevant": ["d1"]}])

    class R:
        name = "fake"

        def search(self, q, k):
            return []

    out = run_evals.retrieval_metrics(base, retriever=R())
    assert out is not None and out["cases"] == 1


def test_calibracao_do_juiz_le_a_familia(tmp_path):
    base = tmp_path / "judge_calibration.jsonl"
    _w(base, [{"id": "1"}])
    _w(tmp_path / "judge_calibration.d" / "c2.jsonl", [{"id": "2"}])
    assert [r["id"] for r in judge.load_calibration(base)] == ["1", "2"]


def test_exemplos_do_roteador_leem_a_familia(tmp_path):
    intent = next(iter(router.PLANS))
    _w(tmp_path / "intent_examples.jsonl", [{"text": "um", "intent": intent}])
    _w(tmp_path / "intent_examples.d" / "c1.jsonl", [{"text": "dois", "intent": intent}])
    got = router._examples_for(str(tmp_path), router._stamp(tmp_path))
    assert [t for t, _ in got] == ["um", "dois"]
