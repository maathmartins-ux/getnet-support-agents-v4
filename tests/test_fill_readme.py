"""Item 11 — fill_readme: blocos offline, ao vivo por split, juiz e replan; idempotente; ausência declarada."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fill_readme  # noqa: E402

from evals.run_evals import run  # noqa: E402

TEMPLATE = "\n".join(f"<!-- {t}:begin -->\n<!-- {t}:end -->" for t in
                     ("eval", "eval-live", "judge", "replan", "timeline"))


@pytest.fixture
def results(tmp_path):
    d = tmp_path / "results"
    run(out=d / "latest.json")
    return d


def test_render_fills_every_block_and_is_idempotent(results, tmp_path):
    report = json.loads((results / "latest.json").read_text(encoding="utf-8"))
    report.update(live=True, provider="anthropic", status="complete", spent_usd=0.42, max_usd=3.0, split="replan")
    (results / "live_replan.json").write_text(json.dumps(report), encoding="utf-8")
    (results / "judge.json").write_text(json.dumps({
        "status": "ran", "model": "claude-opus-5-5", "live_files": ["live_replan.json"], "spent_usd": 0.1,
        "cases": 3, "grounded_rate": 1.0, "helpful_rate": 1.0, "mean_score": 4.5,
        "by_split": {"replan": {"cases": 3, "grounded_rate": 1.0, "helpful_rate": 1.0, "mean_score": 4.5}},
        "calibration": {"cases": 6, "agreement": 1.0, "cohen_kappa": 1.0, "misses": [], "by_split": {}}}),
        encoding="utf-8")
    once = fill_readme.render(TEMPLATE, results, root=tmp_path)
    twice = fill_readme.render(once, results, root=tmp_path)
    assert once == twice
    assert "Gate (splits" in once and "Split `replan`" in once and "US$ 0.42" in once
    assert "kappa de Cohen **1.0**" in once
    assert "| offline | 3 | 100.0 [IC95 43.9–100.0; n=3] |" in once and "faq:no_source->escalation" in once


def test_replan_and_judge_blocks_carry_ci_and_outcome_metric(results, tmp_path):
    # itens 6 e 8 da auditoria v3: toda taxa com n e IC de Wilson; handoff por desfecho AO LADO do flag
    (results / "judge.json").write_text(json.dumps({
        "status": "ran", "model": "m", "live_files": [], "spent_usd": 0.1, "cases": 4, "grounded_rate": 0.5,
        "helpful_rate": 1.0, "mean_score": 4.0, "ci": {"grounded_rate": {"k": 2, "n": 4, "lo": 15.0, "hi": 85.0},
                                                        "helpful_rate": {"k": 4, "n": 4, "lo": 51.0, "hi": 100.0}},
        "by_split": {}, "calibration": {"cases": 6, "agreement": 1.0, "cohen_kappa": 1.0, "misses": [],
                                        "ci": {"agreement": {"k": 6, "n": 6, "lo": 61.0, "hi": 100.0}},
                                        "by_split": {}}}), encoding="utf-8")
    text = fill_readme.render(TEMPLATE, results, root=tmp_path)
    replan = text.split("<!-- replan:begin -->")[1].split("<!-- replan:end -->")[0]
    assert "handoff_outcome_acc" in replan and "IC95" in replan
    judge = text.split("<!-- judge:begin -->")[1].split("<!-- judge:end -->")[0]
    assert "0.5 [IC95 15.0–85.0; n=4]" in judge and "**1.0** [IC95 61.0–100.0; n=6]" in judge


def test_missing_live_and_judge_are_declared(results, tmp_path):
    text = fill_readme.render(TEMPLATE, results, root=tmp_path)
    assert "Eval ao vivo não rodado" in text and "Juiz não rodado" in text


def test_required_marker_missing_fails(results):
    with pytest.raises(SystemExit):
        fill_readme.render("<!-- eval:begin -->\n<!-- eval:end -->", results)


def test_kit_block_measures_scaffold_versus_clock(tmp_path):
    # pergunta 5 do feedback v2: "quanto do código veio do kit prévio?" — respondida com número do git
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       check=True, capture_output=True)

    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "api.py").write_text("a\nb\nc\n", encoding="utf-8")
    git("init", "-b", "main")
    git("add", "-A")
    git("commit", "-m", "chore: scaffold from kit v3.3.0")
    (tmp_path / "src" / "app" / "api.py").write_text("a\nB\nc\nd\n", encoding="utf-8")
    (tmp_path / "src" / "app" / "tools.py").write_text("x\ny\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "feat: tools do domínio")
    out = fill_readme.kit_block(tmp_path)
    assert "| `src/app` | 3 | 4 | 1 | 1 |" in out
    assert "**3** linhas do kit" in out and "**4** escritas no relógio" in out and "(57% do total)" in out


def test_kit_block_without_scaffold_is_declared(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    assert "Sem commit de scaffold" in fill_readme.kit_block(tmp_path)
