"""Check RÁPIDO de uma frente: só o que ela possui, em segundos. O check completo roda uma vez, na integração.

Uso (dentro da sessão/worktree da frente, antes de cada push):
  python scripts/fork_check.py <frente> [--base origin/main]

Etapas:
1. posse: nenhum arquivo fora de OWNERSHIP.json[forks][<frente>] nem congelado (mesma regra do merge);
2. ruff só nos .py alterados;
3. JSONL alterados: cada linha é JSON válido; casos sem id duplicado e com os campos do contrato;
4. pytest só nos testes que a frente possui (tests/test_domain_*.py, test_tools.py ...), em série;
5. se a frente mexeu em casos do eval: eval offline só dos splits dela, gravado em var/ (nunca em evals/results).

Getnet v3: 8 forks rodando o check COMPLETO ao mesmo tempo afogaram a CPU (p95 de 21 s no teste de carga, que
não valeu) e cada merge esperava minutos. Na nuvem cada sessão tem a sua máquina, mas o check completo continua
sendo trabalho da integração: a frente prova a parte dela e devolve rápido.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def changed(base: str) -> list[str]:
    committed = sh(["git", "diff", "--name-only", f"{base}...HEAD"]).stdout.split()
    working = sh(["git", "status", "--porcelain"]).stdout.splitlines()
    return sorted(set(committed) | {line[3:].strip() for line in working if line.strip()})


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("front")
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "OWNERSHIP.json").read_text(encoding="utf-8"))
    owned, frozen = cfg["forks"].get(args.front), cfg.get("frozen", [])
    if owned is None:
        sys.exit(f"ERRO: frente '{args.front}' não está em OWNERSHIP.json; existem: {sorted(cfg['forks'])}")
    t0 = time.perf_counter()
    files = changed(args.base)
    report: list[tuple[str, bool, str]] = []

    bad = [f for f in files if any(fnmatchcase(f, g) for g in frozen) or not any(fnmatchcase(f, g) for g in owned)]
    report.append(("posse", not bad, ", ".join(bad) or f"{len(files)} arquivo(s), todos da frente"))

    py = [f for f in files if f.endswith(".py") and (ROOT / f).exists()]
    lint = sh([PY, "-m", "ruff", "check", *py]) if py else None
    report.append(("ruff", lint is None or lint.returncode == 0, (lint.stdout[-800:] if lint else "nada a checar")))

    broken = []
    for f in (f for f in files if f.endswith(".jsonl") and (ROOT / f).exists()):
        for n, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    broken.append(f"{f}:{n} {exc.msg}")
    touches_cases = any(f.startswith("evals/cases") or f.startswith("evals/heldout") for f in files)
    if touches_cases:
        r = sh([PY, "-c", "import sys; sys.path.insert(0, '.'); from evals.run_evals import load_cases; "
                          "c = load_cases(); print(len(c), ' '.join(sorted({x['split'] for x in c})))"])
        if r.returncode:
            broken.append(r.stderr.strip().splitlines()[-1])
    report.append(("jsonl e contrato dos casos", not broken, "; ".join(broken[:10]) or "ok"))

    tests = sorted({f for f in files if f.startswith("tests/") and f.endswith(".py") and (ROOT / f).exists()}
                   | {g for g in owned if g.startswith("tests/") and "*" not in g and (ROOT / g).exists()})
    pt = sh([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]) if tests else None
    tail = pt.stdout.strip().splitlines()[-1] if pt and pt.stdout.strip() else "sem testes da frente"
    # código 5 = nenhum teste coletado: não é falha da frente
    report.append(("testes da frente", pt is None or pt.returncode in (0, 5), tail))

    if touches_cases:
        splits = sorted({json.loads(line).get("split", "") for f in files if f.startswith("evals/cases")
                         and (ROOT / f).exists() for line in (ROOT / f).read_text(encoding="utf-8").splitlines()
                         if line.strip()})
        for split in splits:
            ev = sh([PY, "-m", "evals.run_evals", "--split", split, "--out", str(ROOT / "var" / f"fork-{split}.json")])
            # informativo: o gate é da integração; aqui a frente só vê o número do split dela
            report.append((f"eval do split {split} (informativo)", True,
                           (ev.stdout.strip().splitlines() or ["(sem saída)"])[-1][:200]))

    print(f"\n| etapa ({args.front}) | resultado | detalhe |\n|---|---|---|")
    for name, ok, detail in report:
        print(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail.replace(chr(10), ' ')[:300]} |")
    failed = [r for r in report if not r[1]]
    print(f"\nFORK CHECK {'VERDE' if not failed else 'VERMELHO'} em {time.perf_counter() - t0:.1f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
