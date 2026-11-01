"""live_eval.ps1: `-Splits a,b` via `powershell -File` chega como UM texto ("a,b"), não como lista.

Smoke de 31/10/2026: o exemplo do cabeçalho rodaria zero casos. -DryRun mostra o que rodaria sem gastar.
Roda onde houver PowerShell/bash, inclusive no CI Linux (pwsh e bash vêm no runner): o intérprete vai por
LIVE_EVAL_PY, porque lá não existe .venv (Getnet v3: o pulo "sem venv" escondia o teste justo no CI).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_eval.ps1"
PS = shutil.which("powershell") or shutil.which("pwsh")
ENV = {**os.environ, "LIVE_EVAL_PY": sys.executable}


@pytest.mark.skipif(PS is None, reason="sem PowerShell")
def test_comma_separated_splits_become_a_list():
    out = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                          "-Splits", "official,replan", "-MaxUsd", "0.15", "-DryRun"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120, env=ENV)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "DRYRUN splits=official|replan teto=0.15" in out.stdout


def _bash() -> str | None:
    found = shutil.which("bash")
    if found and "system32" in found.lower():  # bash.exe do WSL não entende C:\...; o Git Bash entende
        git = Path(r"C:\Program Files\Git\bin\bash.exe")
        return str(git) if git.exists() else None
    return found


BASH = _bash()
SH = Path(__file__).resolve().parents[1] / "scripts" / "live_eval.sh"


@pytest.mark.skipif(BASH is None, reason="sem bash")
def test_bash_twin_parses_the_same_way():
    # feedback v2: "instruções centradas em Windows/PowerShell" → o mesmo fluxo em bash, mesma saída
    out = subprocess.run([BASH, str(SH), "--splits", " official, ,replan", "--max-usd", "0.15", "--repeat", "2",
                          "--dry-run"], capture_output=True, text=True, encoding="utf-8", timeout=120, env=ENV)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "DRYRUN splits=official|replan teto=0.15 workers=4 repeat=2 juiz=True" in out.stdout


@pytest.mark.skipif(BASH is None, reason="sem bash")
def test_bash_rejects_unknown_option():
    out = subprocess.run([BASH, str(SH), "--nada"], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         env=ENV)
    assert out.returncode == 64


@pytest.mark.skipif(BASH is None, reason="sem bash")
def test_missing_interpreter_fails_with_clear_message(tmp_path):
    env = {**os.environ, "LIVE_EVAL_PY": str(tmp_path / "nao-existe")}
    copy = tmp_path / "scripts"
    copy.mkdir()
    shutil.copy(SH, copy / "live_eval.sh")
    out = subprocess.run([BASH, str(copy / "live_eval.sh"), "--dry-run"], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, env=env)
    assert out.returncode == 1 and "LIVE_EVAL_PY" in out.stdout
