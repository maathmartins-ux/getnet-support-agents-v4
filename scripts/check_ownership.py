"""Recusa o merge de um fork que tocou arquivo fora da sua posse ou arquivo congelado.

Uso (na sessão principal, antes de `git merge --no-ff fork/<nome>`):
  python scripts/check_ownership.py <nome-do-fork> [--base main]

Lê OWNERSHIP.json e compara com `git diff --name-status <base>...fork/<nome>` — inclui
arquivos apagados e renomeados, que foi como um fork derrubou o índice em 30/10/2026.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def changed_paths(base: str, branch: str) -> list[tuple[str, str]]:
    r = subprocess.run(["git", "diff", "--name-status", f"{base}...{branch}"],
                       cwd=ROOT, check=False, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"ERRO: git diff falhou ({r.stderr.strip()}). A branch {branch} existe?")
    out = []
    for line in r.stdout.splitlines():
        status, *paths = line.split("\t")
        out += [(status[0], p) for p in paths]  # rename (R) traz origem e destino: os dois contam
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # console cp1252 do Windows quebra acentos
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("fork")
    ap.add_argument("--base", default="main")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "OWNERSHIP.json").read_text(encoding="utf-8"))
    forks = cfg.get("forks", {})
    if args.fork not in forks:
        sys.exit(f"ERRO: fork '{args.fork}' não está em OWNERSHIP.json; existem: {sorted(forks)}")
    allowed, frozen = forks[args.fork], cfg.get("frozen", [])

    violations = []
    changes = changed_paths(args.base, f"fork/{args.fork}")
    for status, path in changes:
        if any(fnmatchcase(path, g) for g in frozen):
            violations.append(f"{status} {path}  (congelado)")
        elif not any(fnmatchcase(path, g) for g in allowed):
            violations.append(f"{status} {path}  (fora da posse de {args.fork})")

    if violations:
        print(f"RECUSADO: fork/{args.fork} tocou {len(violations)} arquivo(s) indevido(s):")
        print("\n".join("  " + v for v in violations))
        return 1
    print(f"OK: fork/{args.fork} alterou {len(changes)} arquivo(s), todos dentro da posse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
