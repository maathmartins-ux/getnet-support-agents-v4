"""Preenche o README com números lidos do eval, do juiz e do git log — nunca digitados à mão.

Uso: python scripts/fill_readme.py   (depois de `python -m evals.run_evals`; ao vivo e juiz são opcionais)

Blocos (texto entre <!-- tag:begin --> e <!-- tag:end -->):
  eval       obrigatório · evals/results/latest.json (offline, gate do CI)
  timeline   obrigatório · git log (minutos do primeiro ao último commit)
  eval-live  opcional    · evals/results/live_<split>.json, uma tabela por split
  judge      opcional    · evals/results/judge.json (juiz sobre o ao vivo + calibração, kappa)
  replan     opcional    · split `replan` do offline e do ao vivo + exemplos de replanejamento
  kit        opcional    · git: linhas do scaffold declarado × linhas escritas no relógio, por pasta
Arquivo ausente vira "não rodado" (declarado, não escondido). Idempotente: rodar duas vezes dá o mesmo arquivo.

`--check` (roda no scripts/check.py): NÃO escreve; falha se os blocos de número do README não forem exatamente o
que `evals/results/` COMMITADO gera, ou se a linha do tempo não tiver todos os commits (tolera só o último, o do
próprio README). Getnet v3: a §5 vinha de um eval não commitado (replan 80% no README, 10% no JSON) e a linha do
tempo parou antes do último commit ("50 min" em vez de 54).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.run_evals import fmt_cell, fmt_ci, to_markdown  # noqa: E402

REQUIRED = ("eval", "timeline")


def timeline(root: Path = ROOT) -> str:
    r = subprocess.run(["git", "log", "--reverse", "--format=%aI|%s"], cwd=root, check=False, text=True,
                       capture_output=True, encoding="utf-8")
    rows = [line.split("|", 1) for line in r.stdout.splitlines() if "|" in line]
    if not rows:
        return "_Sem commits ainda._"
    first, last = datetime.fromisoformat(rows[0][0]), datetime.fromisoformat(rows[-1][0])
    lines = ["| hora (UTC; Brasília = UTC−3) | min | commit |", "|---|---:|---|"]
    for ts, subject in rows:
        t = datetime.fromisoformat(ts)
        lines.append(f"| {t:%H:%M:%S} | {(t - first).total_seconds() / 60:.0f} | {subject} |")
    lines.append(f"\nDo primeiro ao último commit: **{(last - first).total_seconds() / 60:.0f} min**.")
    return "\n".join(lines)


def _load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def offline_block(results: Path) -> str:
    report = _load(results / "latest.json")
    if not report:
        sys.exit("ERRO: rode `python -m evals.run_evals` antes; evals/results/latest.json não existe.")
    return f"Modo: `{report['mode']}` · provedor `{report.get('provider', 'offline')}`\n\n" + to_markdown(report)


def live_block(results: Path) -> str:
    files = sorted(results.glob("live_*.json"))
    if not files:
        return "_Eval ao vivo não rodado (sem chave no relógio ou cortado). Ver Limitações._"
    parts = []
    for f in files:
        r = _load(f)
        parts.append(f"### Split `{r.get('split', f.stem)}` — `{f.name}`\n\n"
                     f"Provedor `{r.get('provider')}` · status `{r.get('status')}` · "
                     f"gasto US$ {r.get('spent_usd')} (teto {r.get('max_usd')})\n\n" + to_markdown(r))
    return "\n\n".join(parts)


def judge_block(results: Path) -> str:
    j = _load(results / "judge.json")
    if not j:
        return "_Juiz não rodado._"
    if j.get("status") in ("skipped", "error"):
        return f"Juiz: status `{j['status']}` — {j.get('reason', '')}"
    cal = j.get("calibration", {})

    def ci(block: dict, key: str) -> str:  # juiz antigo (sem "ci") sai sem intervalo, não quebra
        found = (block.get("ci") or {}).get(key)
        return f" {fmt_ci(found)}" if found else ""

    lines = [f"Modelo `{j['model']}` · status `{j['status']}` · arquivos {', '.join(j.get('live_files', [])) or '—'}"
             f" · gasto US$ {j.get('spent_usd')}", "",
             "| split | casos | grounded | helpful | nota média |", "|---|---:|---:|---:|---:|"]
    for split, s in {**j.get("by_split", {}), "ALL": j}.items():
        lines.append(f"| {split} | {s['cases']} | {s['grounded_rate']}{ci(s, 'grounded_rate')} | "
                     f"{s['helpful_rate']}{ci(s, 'helpful_rate')} | {s['mean_score']} |")
    lines += ["", f"Calibração humana: {cal.get('cases')} rótulos · concordância **{cal.get('agreement')}**"
              f"{ci(cal, 'agreement')} · kappa de Cohen **{cal.get('cohen_kappa')}** · TPR {cal.get('tpr')}"
              f"{ci(cal, 'tpr')} ({cal.get('positives')} grounded) · TNR {cal.get('tnr')}{ci(cal, 'tnr')} "
              f"({cal.get('negatives')} não grounded) · discordâncias: {', '.join(cal.get('misses', [])) or '—'}"]
    for split, s in cal.get("by_split", {}).items():
        lines.append(f"- {split}: concordância {s['agreement']}{ci(s, 'agreement')} · kappa {s['cohen_kappa']} "
                     f"({s['cases']} rótulos)")
    return "\n".join(lines)


def replan_block(results: Path) -> str:
    sources = [("offline", _load(results / "latest.json"))] + [
        (f"ao vivo ({f.name})", _load(f)) for f in sorted(results.glob("live_*.json"))] + [
        ("offline SEM replanejamento", _load(results / "ablation_replan.json")),
        ("ao vivo SEM replanejamento", _load(results / "ablation_replan_live.json"))]
    keys = ("replanned_acc", "handoff_acc", "handoff_outcome_acc", "intent_acc")
    lines = ["| fonte | casos | " + " | ".join(keys) + " |", "|---|---:|" + "---:|" * len(keys)]
    examples: list[str] = []
    for name, r in sources:
        m = (r or {}).get("metrics", {}).get("replan")
        if not m:
            continue
        lines.append(f"| {name} | {m['cases']} | " + " | ".join(fmt_cell(m, k) for k in keys) + " |")
        for row in r["rows"]:
            if row["split"] == "replan" and row.get("replans") and len(examples) < 5:
                examples.append(f"- `{row['id']}` ({name}): {' · '.join(row['replans'])}")
    if len(lines) == 2:
        return "_Sem split `replan` nos resultados._"
    gain = _ablation_gain(results)
    return "\n".join(lines + ([""] + examples if examples else []) + ([""] + gain if gain else []))


def _ablation_gain(results: Path) -> list[str]:
    """Quanto o replanejamento vale: handoff/desfecho certo COM × SEM, nos mesmos casos (ablação)."""
    out = []
    for name, with_f, without_f in (("offline", "latest.json", "ablation_replan.json"),
                                    ("ao vivo", None, "ablation_replan_live.json")):
        without = _load(results / without_f)
        live = [_load(f) or {} for f in sorted(results.glob("live_*.json"))]
        with_r = _load(results / with_f) if with_f else next((r for r in live if r.get("metrics", {}).get("replan")),
                                                              None)
        a = (with_r or {}).get("metrics", {}).get("replan")
        b = (without or {}).get("metrics", {}).get("replan")
        if a and b and a.get("handoff_outcome_acc") is not None and b.get("handoff_outcome_acc") is not None:
            out.append(f"Ablação ({name}): desfecho certo no split replan **{a['handoff_outcome_acc']:.1f}% com** × "
                       f"**{b['handoff_outcome_acc']:.1f}% sem** replanejamento (mesmos {a['cases']} casos).")
    return out


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # hash do tree vazio do git: diff dele = conteúdo inteiro


def _numstat(root: Path, a: str, b: str) -> list[tuple[int, int, str]]:
    r = subprocess.run(["git", "diff", "--numstat", a, b], cwd=root, check=False, text=True,
                       capture_output=True, encoding="utf-8")
    rows = []
    for line in r.stdout.splitlines():
        add, rem, path = line.split("\t", 2)
        if add != "-":  # binário não tem linhas
            rows.append((int(add), int(rem), path))
    return rows


# Arquivos gerados não contam como "escrito no relógio": a v3 dizia 72% com 21.909 linhas de JSON de resultado.
GENERATED = ("evals/results/",)


def kit_block(root: Path = ROOT) -> str:
    """Quanto veio do kit declarado e quanto foi escrito no relógio, medido pelo git (pergunta 5 do feedback v2)."""
    r = subprocess.run(["git", "log", "--reverse", "--format=%H|%s"], cwd=root, check=False, text=True,
                       capture_output=True, encoding="utf-8")
    commits = [line.split("|", 1) for line in r.stdout.splitlines() if "|" in line]
    scaffold = next((h for h, subject in commits if subject.startswith("chore: scaffold")), None)
    if not scaffold:
        return "_Sem commit de scaffold declarado (`chore: scaffold ...`)._"
    kit = _numstat(root, EMPTY_TREE, scaffold)
    clock_all = _numstat(root, scaffold, "HEAD")
    clock = [row for row in clock_all if not row[2].startswith(GENERATED)]
    generated = sum(add for add, _, path in clock_all if path.startswith(GENERATED))
    kit_files = {path for _, _, path in kit}

    def top(path: str) -> str:
        parts = path.split("/")
        nested = parts[0] in ("src", "evals", "tests", "data", "docs") and len(parts) > 2
        return "/".join(parts[:2]) if nested else parts[0]

    table: dict[str, list[int]] = {}
    for add, _, path in kit:
        table.setdefault(top(path), [0, 0, 0, 0])[0] += add
    for add, rem, path in clock:
        row = table.setdefault(top(path), [0, 0, 0, 0])
        row[1] += add
        row[2] += rem
        row[3] += path in kit_files
    lines = ["| pasta | linhas do kit | + no relógio | − no relógio | arquivos do kit alterados |",
             "|---|---:|---:|---:|---:|"]
    for folder, (k, a, d, m) in sorted(table.items()):
        lines.append(f"| `{folder}` | {k} | {a} | {d} | {m} |")
    k_tot, a_tot = sum(x[0] for x in table.values()), sum(x[1] for x in table.values())
    share = a_tot / (k_tot + a_tot) if k_tot + a_tot else 0
    lines.append(f"\nTotal: **{k_tot}** linhas do kit (commit `{scaffold[:7]}`) e **{a_tot}** escritas no relógio "
                 f"({share:.0%} do total). Arquivos do kit alterados no relógio: "
                 f"{sum(x[3] for x in table.values())}. Fora da conta, por serem gerados: {generated} linhas "
                 f"em `{'`, `'.join(GENERATED)}`.")
    return "\n".join(lines)


BLOCKS = {"eval": offline_block, "eval-live": live_block, "judge": judge_block, "replan": replan_block}


def replace_block(text: str, tag: str, body: str) -> str:
    pattern = re.compile(rf"(<!-- {re.escape(tag)}:begin -->)(.*?)(<!-- {re.escape(tag)}:end -->)", re.DOTALL)
    return pattern.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", text)


def render(text: str, results: Path, root: Path = ROOT) -> str:
    for tag in REQUIRED:
        if f"<!-- {tag}:begin -->" not in text:
            sys.exit(f"ERRO: README sem os marcadores <!-- {tag}:begin --> e <!-- {tag}:end -->.")
    for tag, fn in BLOCKS.items():
        if f"<!-- {tag}:begin -->" in text:
            text = replace_block(text, tag, fn(results))
    if "<!-- kit:begin -->" in text:
        text = replace_block(text, "kit", kit_block(root))
    return replace_block(text, "timeline", timeline(root))


def _block(text: str, tag: str) -> str | None:
    m = re.search(rf"<!-- {re.escape(tag)}:begin -->(.*?)<!-- {re.escape(tag)}:end -->", text, re.DOTALL)
    return m.group(1).strip() if m else None


README_ONLY = {"README.md", "ESTADO.md"}  # o commit do próprio README pode não estar na linha do tempo


def check(readme: Path, results: Path, root: Path = ROOT) -> list[str]:
    """Divergências entre o README e o que a evidência commitada gera. Lista vazia = README honesto."""
    text = readme.read_text(encoding="utf-8")
    problems = []
    for tag, fn in BLOCKS.items():
        current = _block(text, tag)
        if current is None:
            continue
        if not current:  # bloco vazio: nada afirmado ainda (kit antes do desafio)
            continue
        if tag == "eval" and not (results / "latest.json").exists():
            problems.append("bloco eval preenchido, mas evals/results/latest.json não está no repositório")
            continue
        if current != fn(results).strip():
            problems.append(f"bloco {tag} diferente do que evals/results/ gera: rode scripts/fill_readme.py e "
                            "commite README e resultados juntos")
    rows = _block(text, "timeline")
    if rows:
        r = subprocess.run(["git", "log", "--format=%s"], cwd=root, check=False, text=True,
                           capture_output=True, encoding="utf-8")
        commits = [c for c in r.stdout.splitlines() if c.strip()]
        listed = sum(1 for line in rows.splitlines() if re.match(r"^\| \d\d:\d\d:\d\d \|", line))
        last_files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=root, check=False,
                                    text=True, capture_output=True, encoding="utf-8").stdout.split()
        readme_only = bool(last_files) and set(last_files) <= README_ONLY
        if listed < len(commits) - (1 if readme_only else 0):
            problems.append(f"linha do tempo com {listed} commits, o git tem {len(commits)} (o último mexeu em "
                            f"{', '.join(last_files[:4]) or 'nada'}): rode scripts/fill_readme.py como ÚLTIMO passo "
                            "e commite só README/ESTADO por cima")
    return problems


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # console cp1252 do Windows quebra acentos
        stream.reconfigure(encoding="utf-8")
    readme = ROOT / "README.md"
    if "--check" in sys.argv:
        problems = check(readme, ROOT / "evals" / "results")
        for p in problems:
            print(f"README ≠ evidência: {p}")
        print("OK: README bate com evals/results/ e com o git log." if not problems else "")
        return 1 if problems else 0
    text = render(readme.read_text(encoding="utf-8"), ROOT / "evals" / "results")
    readme.write_text(text, encoding="utf-8")
    print("OK: README atualizado (eval, ao vivo, juiz, replan, linha do tempo).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
