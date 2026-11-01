"""Hook de posse (.claude/hooks/ownership_guard.py): fork só edita o que é dele, na hora da edição.

Monta um repositório git temporário com OWNERSHIP.json, troca de branch e roda o hook como o Claude Code
roda (JSON no stdin), conferindo a decisão.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "ownership_guard.py"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="sem git")


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "OWNERSHIP.json").write_text(json.dumps(
        {"frozen": ["src/app/schemas.py"], "forks": {"a": ["data/knowledge/**", "tests/test_ingest.py"]}}),
        encoding="utf-8")
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    return tmp_path


def run_hook(repo, rel):
    event = {"tool_name": "Write", "tool_input": {"file_path": str(repo / rel)}, "cwd": str(repo)}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0
    return json.loads(out.stdout)["hookSpecificOutput"] if out.stdout.strip() else None


def test_main_branch_is_free(repo):
    assert run_hook(repo, "src/app/schemas.py") is None


def test_fork_edits_its_own_files_only(repo):
    git(repo, "checkout", "-b", "fork/a")
    assert run_hook(repo, "data/knowledge/chunks.jsonl") is None
    denied = run_hook(repo, "src/app/tools.py")
    assert denied["permissionDecision"] == "deny" and "não é do fork a" in denied["permissionDecisionReason"]
    frozen = run_hook(repo, "src/app/schemas.py")
    assert frozen["permissionDecision"] == "deny" and "congelado" in frozen["permissionDecisionReason"]


def test_path_outside_repo_and_broken_input_never_block(repo, tmp_path_factory):
    git(repo, "checkout", "-b", "fork/a")
    outside = tmp_path_factory.mktemp("scratch") / "nota.md"
    event = {"tool_name": "Write", "tool_input": {"file_path": str(outside)}, "cwd": str(repo)}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True)
    assert out.returncode == 0 and not out.stdout.strip()
    bad = subprocess.run([sys.executable, str(HOOK)], input="isto não é json", capture_output=True, text=True)
    assert bad.returncode == 0 and not bad.stdout.strip()


def test_settings_wire_the_hook():
    cfg = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entry = cfg["hooks"]["PreToolUse"][0]
    assert "Edit" in entry["matcher"] and "Write" in entry["matcher"]
    assert "ownership_guard.py" in entry["hooks"][0]["command"]
