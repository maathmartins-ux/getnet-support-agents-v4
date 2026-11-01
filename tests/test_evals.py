"""Gate offline, eval ao vivo por split com teto de US$ (item 8) e juiz com calibração e kappa (item 9)."""
import json
from pathlib import Path

import pytest

from evals import judge, run_evals
from evals.run_evals import load_cases, run

# rótulos do kit: o FakeJudge e os ids do teste são os dele. evals/judge_calibration.jsonl é do domínio e muda
# no relógio (Getnet v3: 34 rótulos novos derrubaram 2 testes do juiz no CI)
KIT_CAL = Path(__file__).parent / "fixtures" / "kit_data" / "judge_calibration.jsonl"


def test_cases_file_is_valid():
    cases = load_cases()
    assert cases, "evals/cases.jsonl vazio"
    assert {c["split"] for c in cases} >= {"official", "replan"}


def test_gate_passes_in_process(tmp_path):
    report = run(out=tmp_path / "latest.json")
    assert report["passed"], report["failures"]
    assert report["retrieval"]["retriever"] == "bm25" and report["metrics"]["replan"]["replanned_acc"] == 100.0


class PricedClient:
    """Cliente falso do eval: resposta fixa com custo fixo por chamada (padrão US$ 0,40)."""

    def __init__(self, cost=0.4):
        self.calls, self.cost = [], cost

    def send(self, message, user_id, conversation_id):
        self.calls.append(message)
        return {"intent": "faq", "conversation_id": "c", "answer": "ok", "replans": [], "route": ["router", "faq"],
                "usage": {"cost_usd": self.cost}}


def test_live_without_credentials_exits_cleanly(monkeypatch, tmp_path, capsys):
    for var in ("APP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_IDENTITY_TOKEN_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(run_evals.Path, "home", lambda: tmp_path)
    assert run_evals.main(["--live", "--split", "official"]) == 2
    out = capsys.readouterr().out
    assert "SEM CREDENCIAL" in out and "Traceback" not in out


def test_live_stops_at_cost_cap_and_writes_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    client = PricedClient()
    report = run(split="official", live=True, max_usd=1.0, client=client)
    # 0,4 + 0,4 = 0,8; o terceiro caso projetado (0,4) passaria de 1,0 → para ANTES de gastar
    assert report["status"] == "aborted_max_usd" and len(client.calls) == 2
    saved = json.loads((tmp_path / "live_official.json").read_text(encoding="utf-8"))
    assert saved["status"] == "aborted_max_usd" and len(saved["rows"]) == 2 and saved["spent_usd"] == 0.8
    assert saved["provider"] == "anthropic"


def test_parallel_wave_shrinks_to_fit_the_cap(monkeypatch, tmp_path):
    # smoke de 31/10/2026: leva de 3 casos lançada com US$ 0,0258 de saldo gastou 0,0275
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    client = PricedClient(cost=0.01)
    report = run(split="official", live=True, max_usd=0.03, workers=4, client=client)
    assert len(client.calls) == 2 and report["spent_usd"] <= 0.03 and report["status"] == "aborted_max_usd"


def test_live_workers_keep_case_order(monkeypatch, tmp_path):
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    report = run(live=True, workers=3, client=PricedClient())
    assert [r["id"] for r in report["rows"]] == [c["id"] for c in load_cases()]
    assert report["status"] == "complete" and (tmp_path / "live_all.json").exists()


# --- juiz ------------------------------------------------------------------------------------------
class FakeJudge:
    model = "fake-judge"

    def complete(self, system, messages, max_tokens=1024, schema=None):
        from app.llm import LLMReply
        from app.observability import record_llm_usage

        record_llm_usage("claude-opus-5-5", 100_000, 10_000)  # US$ 0,60 por chamada
        grounded = not any(s in messages[0]["content"] for s in ("24 horas", "30 dias", "Ulan Bator"))
        return LLMReply(json.dumps({"grounded": grounded, "helpful": True, "score": 4, "reason": "ok"}), self.model)


def live_file(tmp_path):
    path = tmp_path / "live_official.json"
    path.write_text(json.dumps({"rows": [
        {"id": "a", "split": "official", "message": "Prazo?", "answer": "3 a 5 dias úteis.", "source_ids": []},
        {"id": "b", "split": "replan", "message": "Mongólia?", "answer": "Ulan Bator", "source_ids": []}]}),
        encoding="utf-8")
    return path


def test_judge_rates_live_answers_and_calibrates(tmp_path):
    report = judge.run(FakeJudge(), [live_file(tmp_path)], calibration=KIT_CAL)
    assert report["status"] == "ran" and report["cases"] == 2 and report["grounded_rate"] == 0.5
    assert report["by_split"]["replan"]["grounded_rate"] == 0.0
    cal = report["calibration"]
    assert cal["agreement"] == 1.0 and cal["cohen_kappa"] == 1.0 and set(cal["by_split"]) == {"official", "replan"}


def test_judge_stops_at_cost_cap(tmp_path):
    report = judge.run(FakeJudge(), [live_file(tmp_path)], calibration=KIT_CAL, max_usd=1.0)
    # 0,60 gasto; a segunda chamada projetada (0,60) passaria de 1,0 → para antes
    assert report["status"] == "aborted_max_usd" and report["spent_usd"] == pytest.approx(0.6)


def test_judge_offline_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "OUT", tmp_path / "judge.json")
    assert judge.main([]) == 0
    assert json.loads((tmp_path / "judge.json").read_text(encoding="utf-8"))["status"] == "skipped"


def test_cohen_kappa():
    from evals.judge import cohen_kappa

    assert cohen_kappa([(True, True), (False, False)] * 5) == 1.0
    assert cohen_kappa([(True, True), (True, False), (False, True), (False, False)]) == 0.0
    assert cohen_kappa([]) is None


def test_tpr_tnr_by_class():
    # Hamel Husain, Evals FAQ (21/09/2026): validar o juiz por classe, não só concordância/kappa
    from evals.judge import tpr_tnr

    pairs = [(True, True)] * 3 + [(False, True)] + [(False, False)] * 2 + [(True, False)] * 2
    assert tpr_tnr(pairs) == {"tpr": 0.75, "tnr": 0.5, "positives": 4, "negatives": 4}
    assert tpr_tnr([(True, True)]) == {"tpr": 1.0, "tnr": None, "positives": 1, "negatives": 0}


def test_calibration_reports_tpr_tnr(tmp_path):
    cal = judge.run(FakeJudge(), [live_file(tmp_path)], calibration=KIT_CAL)["calibration"]
    assert cal["tpr"] == 1.0 and cal["tnr"] == 1.0 and cal["positives"] == 3 and cal["negatives"] == 3


class FlakyClient(PricedClient):
    """Erra a intenção na 2ª vez que vê cada mensagem: caso instável, que uma rodada só esconde."""

    def __init__(self):
        super().__init__(cost=0.0)
        self.seen = {}

    def send(self, message, user_id, conversation_id):
        got = super().send(message, user_id, conversation_id)
        self.seen[message] = self.seen.get(message, 0) + 1
        return {**got, "intent": "faq" if self.seen[message] != 2 else "records"}


def test_repeat_reports_pass_hat_k_and_unstable_cases(monkeypatch, tmp_path):
    # Anthropic, "Demystifying evals" (09/01/2026): pass^k = todas as k rodadas passam; mede confiabilidade
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    client = FlakyClient()
    report = run(split="official", live=True, repeat=3, client=client)
    st = report["stability"]
    assert len(report["rows"]) == 3 * len(st["by_case"]) and st["runs"] == 3
    first = {r["id"] for r in report["rows"] if r["run"] == 0 and not r["crash"] and all(r["checks"].values())}
    assert first and set(st["unstable"]) == first  # passaram na 1ª, erraram a intenção na 2ª
    assert all(st["by_case"][i]["passes"] == 2 for i in first)
    assert st["pass_hat_k"] < st["pass_at_1"]


def test_repeat_one_keeps_the_old_report_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    report = run(split="official", live=True, client=PricedClient(cost=0.0))
    assert report["stability"]["runs"] == 1 and report["stability"]["unstable"] == []


def test_offline_gate_checks_numbers():
    report = run()
    flagged = [(r["id"], r["unsupported_numbers"]) for r in report["rows"] if not r["checks"].get("numbers", True)]
    assert report["metrics"]["ALL"]["numbers_acc"] == 100.0, flagged


def test_failure_matrix_and_intent_confusion():
    from evals.run_evals import failure_matrix

    rows = [{"split": "official", "checks": {"intent": False, "tools": False}, "crash": None,
             "expected_intent": "records", "got_intent": "faq"},
            {"split": "official", "checks": {"intent": True, "handoff": False}, "crash": None,
             "expected_intent": "faq", "got_intent": "faq"},
            {"split": "replan", "checks": {"intent": False}, "crash": None,
             "expected_intent": "faq", "got_intent": "multi"}]
    fm = failure_matrix(rows)
    assert fm["by_check"] == {"intent": {"official": 1, "replan": 1}, "tools": {"official": 1},
                              "handoff": {"official": 1}}
    assert fm["intent_confusion"] == {"records": {"faq": 1}, "faq": {"multi": 1}}


def test_report_has_failure_matrix_and_gate_sections(monkeypatch, tmp_path):
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    report = run(split="official", live=True, client=PricedClient(cost=0.0))  # resposta fixa: falhas garantidas
    md = run_evals.to_markdown(report)
    assert report["failure_matrix"]["by_check"] and "Falhas por checagem" in md and "Confusão de intenção" in md
    assert report["gate_calibration"]["best"] and "Portão sem fonte" in md
