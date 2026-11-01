"""Itens 7 e 8: pass^k por padrão no ao vivo e handoff medido pelo desfecho, ao lado da métrica atual."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evals import run_evals
from evals.run_evals import score, summarize

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "LIVE_EVAL_PY": sys.executable}


# --- item 7: --repeat 3 no ao vivo ------------------------------------------------------------------
def _captured_repeat(monkeypatch, argv):
    seen = {}

    def fake_run(*args, **kwargs):
        seen["repeat"] = kwargs["repeat"]
        return {"path": "x", "failures": [], "status": "complete", "passed": True, "rows": []}

    monkeypatch.setattr(run_evals, "run", fake_run)
    monkeypatch.setattr(run_evals, "to_markdown", lambda report: "")
    monkeypatch.setattr(run_evals, "has_credentials", lambda: True)
    run_evals.main(argv)
    return seen["repeat"]


def test_live_defaults_to_three_repeats(monkeypatch):
    assert _captured_repeat(monkeypatch, ["--live", "--split", "official"]) == 3


def test_offline_keeps_one_repeat_and_explicit_value_wins(monkeypatch):
    assert _captured_repeat(monkeypatch, []) == 1
    assert _captured_repeat(monkeypatch, ["--live", "--repeat", "1"]) == 1


def test_help_text_states_the_live_default(capsys):
    with pytest.raises(SystemExit):
        run_evals.main(["--help"])
    assert "padrão 3 no ao vivo" in capsys.readouterr().out


def _bash():
    found = shutil.which("bash")
    if found and "system32" in found.lower():  # bash do WSL não entende C:\...
        git = Path(r"C:\Program Files\Git\bin\bash.exe")
        return str(git) if git.exists() else None
    return found


@pytest.mark.skipif(_bash() is None, reason="sem bash")
def test_bash_script_defaults_to_three_repeats():
    out = subprocess.run([_bash(), str(ROOT / "scripts" / "live_eval.sh"), "--dry-run"], capture_output=True,
                         text=True, encoding="utf-8", timeout=120, env=ENV)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "repeat=3" in out.stdout


PS = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(PS is None, reason="sem PowerShell")
def test_powershell_script_defaults_to_three_repeats():
    out = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(ROOT / "scripts" / "live_eval.ps1"), "-DryRun"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120, env=ENV)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "repeat=3" in out.stdout


# --- item 8: handoff por desfecho -------------------------------------------------------------------
def test_outcome_fails_when_forbidden_escalation_was_called():
    case = {"expect": {"intent": "faq", "handoff": False}}
    got = {"intent": "faq", "route": ["router", "faq", "escalation"], "handoff": {"required": False}}
    checks = score(case, got)
    assert checks["handoff"] is True and checks["handoff_outcome"] is False


def test_outcome_passes_when_required_agent_was_called():
    case = {"expect": {"intent": "faq", "handoff": True}}
    got = {"intent": "faq", "route": ["router", "faq", "escalation"], "handoff": {"required": False}}
    checks = score(case, got)
    assert checks["handoff"] is False and checks["handoff_outcome"] is True


def test_outcome_uses_explicit_required_and_forbidden_agents():
    case = {"expect": {"intent": "multi", "agents": ["faq", "records"], "forbidden_agents": ["escalation"]}}
    assert score(case, {"intent": "multi", "route": ["router", "records", "faq"]})["handoff_outcome"] is True
    assert score(case, {"intent": "multi", "route": ["router", "records"]})["handoff_outcome"] is False
    assert score(case, {"intent": "multi", "route": ["router", "faq", "records", "escalation"]})[
        "handoff_outcome"] is False


def test_outcome_metric_is_published_beside_the_flag_metric():
    rows = [{"split": "official", "ms": 1.0, "cost_usd": 0, "crash": None,
             "checks": {"intent": True, "handoff": True, "handoff_outcome": False}}]
    m = summarize(rows)["official"]
    assert m["handoff_acc"] == 100.0 and m["handoff_outcome_acc"] == 0.0
