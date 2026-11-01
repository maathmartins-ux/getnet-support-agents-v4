"""Hook PreToolUse do Claude Code: fork só edita o que é dele (OWNERSHIP.json), na hora, não só no merge.

O check_ownership.py pega a invasão no merge, quando o estrago (um fork apagando dados de outro, 30/10/2026)
já custou minutos. Este hook recusa a edição no momento em que o fork tenta. Regra igual à do merge:
- branch `fork/<x>` → só caminhos de forks.<x>; caminhos `frozen` nunca;
- qualquer outra branch (main, sessão principal) → livre.

Entrada (stdin): JSON do Claude Code com tool_name, tool_input.file_path (Edit/Write/NotebookEdit) e cwd,
que acompanha o worktree. Saída: JSON com permissionDecision "deny" e o motivo, ou nada (segue o fluxo
normal de permissão). Erro inesperado nunca bloqueia: o merge ainda confere.
Dependências: biblioteca padrão + git no PATH.
"""
from __future__ import annotations

import json
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path


def _git(cwd: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=False, timeout=5)
    return r.stdout.strip() if r.returncode == 0 else ""


def decide(event: dict) -> str | None:
    """Motivo da recusa, ou None para liberar."""
    path = (event.get("tool_input") or {}).get("file_path") or (event.get("tool_input") or {}).get("notebook_path")
    cwd = event.get("cwd") or "."
    if not path:
        return None
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch.startswith("fork/"):
        return None
    root = Path(_git(cwd, "rev-parse", "--show-toplevel") or cwd)
    cfg_file = root / "OWNERSHIP.json"
    if not cfg_file.exists():
        return None
    try:
        rel = Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None  # fora do repositório (scratch, temp): não é posse de ninguém
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    fork = branch.removeprefix("fork/")
    allowed, frozen = cfg.get("forks", {}).get(fork, []), cfg.get("frozen", [])
    if any(fnmatchcase(rel, g) for g in frozen):
        return f"{rel} está congelado (OWNERSHIP.json): só a sessão principal muda"
    if not any(fnmatchcase(rel, g) for g in allowed):
        return f"{rel} não é do fork {fork}; a posse dele é {allowed}. Peça à sessão principal."
    return None


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        reason = decide(event)
    except Exception:  # noqa: BLE001 — hook com defeito não pode travar o relógio; o merge confere
        return 0
    if reason:
        # ASCII com escapes \uXXXX: o Claude Code decodifica igual com console cp1252 ou UTF-8 (acento não quebra)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
