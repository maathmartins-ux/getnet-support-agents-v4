"""LLM-as-judge sobre as respostas AO VIVO, calibrado contra rótulos humanos (concordância e kappa).

Uso:
  python -m evals.judge                                   # todos os evals/results/live_*.json + calibração
  python -m evals.judge evals/results/live_official.json --max-usd 1

Papel "judge" (Opus 5.5, effort medium). Sem LLM_PROVIDER=anthropic grava status "skipped" e sai com
0: o juiz é evidência adicional, não gate. Calibração: evals/judge_calibration.jsonl traz respostas com
rótulo humano (`human_grounded`, `split` opcional); concordância bruta e kappa de Cohen (que desconta o
acaso) dizem quanto confiar no juiz. Grava evals/results/judge.json (lido por scripts/fill_readme.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
RESULTS_DIR = HERE / "results"
OUT = RESULTS_DIR / "judge.json"
CALIBRATION = HERE / "judge_calibration.jsonl"
# Custo estimado por chamada antes da 1ª medida. Smoke ao vivo de 31/10/2026: 10 chamadas do Opus custaram
# US$ 0,0653 (0,0065 cada); 0,02 travou a rodada com saldo sobrando. 0,01 mantém ~50% de folga.
# Depois da 1ª chamada vale o maior entre isto e a média medida.
USD_PER_CALL = 0.01


def load_calibration(path: Path | None = None) -> list[dict]:
    """Rótulos humanos: base + judge_calibration.d/*.jsonl (um arquivo por fork, kit 3.6.0)."""
    from app.jsonl import read_family

    return read_family(path or CALIBRATION)

# Getnet v3 (31/10/2026): o juiz só via os trechos do RAG e marcou 46 de 47 respostas como "não grounded"
# porque usavam dado do CRM ou da web. Dado de ferramenta é fonte: o juiz recebe o que a resposta usou.
SYSTEM = ("Você avalia respostas de um assistente de atendimento. Dadas a pergunta, as fontes (documentos e "
          "páginas web), os dados de ferramenta (consultas ao sistema do cliente: CRM, pedidos, ações) e a "
          "resposta, julgue: grounded = toda afirmação da resposta está nas fontes ou nos dados de ferramenta "
          "(ou a resposta só encaminha para humano / pede dado / recusa); helpful = a resposta atende à "
          "pergunta. score de 1 a 5.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["grounded", "helpful", "score", "reason"],
          "properties": {"grounded": {"type": "boolean"}, "helpful": {"type": "boolean"},
                         "score": {"type": "integer"}, "reason": {"type": "string"}}}


class BudgetExceeded(RuntimeError):
    pass


def _sources_text(ids: list[str], sources: list[dict] | None = None) -> str:
    """Trechos do índice pelo id; fonte fora do índice (web) entra com título e URL, que é o que a API expõe."""
    from app.rag import get_index
    docs = {d.id: d for d in get_index().docs}
    lines = [f"- {docs[i].title}: {docs[i].text}" for i in ids if i in docs]
    lines += [f"- {s.get('title') or s.get('id')} ({s.get('url') or 'sem URL'}) [fonte externa: título e endereço]"
              for s in sources or [] if s.get("id") not in docs]
    return "\n".join(lines) or "(nenhuma fonte)"


def _tool_text(tool_calls: list[dict] | None, evidence: list[str] | None) -> str:
    """Tools chamadas (nome, argumentos, ok) e o texto da evidência quando a linha do eval o traz."""
    lines = [f"- {c.get('name')}({json.dumps(c.get('args', {}), ensure_ascii=False)})"
             f" → {'ok' if c.get('ok', True) else 'falhou'}" for c in tool_calls or []]
    lines += [f"- resultado: {e}" for e in evidence or []]
    return "\n".join(lines) or "(nenhum)"


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Concordância juiz × humano descontado o acaso: (po - pe) / (1 - pe)."""
    if not pairs:
        return None
    n = len(pairs)
    po = sum(j == h for j, h in pairs) / n
    pj, ph = sum(j for j, _ in pairs) / n, sum(h for _, h in pairs) / n
    pe = pj * ph + (1 - pj) * (1 - ph)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def _rates(items: list[dict]) -> dict:
    from evals.run_evals import rate_ci

    n = len(items) or 1
    grounded, helpful = sum(bool(v["grounded"]) for v in items), sum(bool(v["helpful"]) for v in items)
    return {"cases": len(items), "grounded_rate": round(grounded / n, 3),
            "helpful_rate": round(helpful / n, 3),
            "mean_score": round(sum(v["score"] for v in items) / n, 2),
            # n e IC de Wilson a 95% (lo/hi em %)
            "ci": {"grounded_rate": rate_ci(grounded, len(items)), "helpful_rate": rate_ci(helpful, len(items))}}


def tpr_tnr(pairs: list[tuple[bool, bool]]) -> dict:
    """Acerto do juiz por classe do rótulo humano: TPR nos grounded, TNR nos não grounded.

    Concordância alta esconde juiz que aprova tudo; TPR/TNR mostra (Hamel Husain, Evals FAQ, 21/09/2026).
    """
    pos = [j for j, h in pairs if h]
    neg = [j for j, h in pairs if not h]
    return {"tpr": round(sum(pos) / len(pos), 3) if pos else None,
            "tnr": round(sum(not j for j in neg) / len(neg), 3) if neg else None,
            "positives": len(pos), "negatives": len(neg)}


def _agreement(pairs: list[tuple[bool, bool]]) -> dict:
    from evals.run_evals import rate_ci

    pos = [j for j, h in pairs if h]
    neg = [j for j, h in pairs if not h]
    return {"cases": len(pairs), "agreement": round(sum(j == h for j, h in pairs) / len(pairs), 3) if pairs else None,
            "cohen_kappa": cohen_kappa(pairs), **tpr_tnr(pairs),
            "ci": {"agreement": rate_ci(sum(j == h for j, h in pairs), len(pairs)),
                   "tpr": rate_ci(sum(pos), len(pos)), "tnr": rate_ci(sum(not j for j in neg), len(neg))}}


def run(llm, inputs: list[Path], calibration: Path | None = None, max_usd: float | None = None) -> dict:
    calibration = calibration or CALIBRATION  # lido na chamada: o conftest troca pela fixture do kit
    from app.observability import new_trace_id, request_usage

    new_trace_id()  # zera o contador de custo desta execução
    calls = [0]

    def judge_one(item: dict, question: str, answer: str) -> dict:
        spent = request_usage()["cost_usd"]
        est = max(USD_PER_CALL, spent / calls[0] if calls[0] else 0.0)
        if max_usd is not None and spent + est > max_usd + 1e-9:  # projeta: só chama se a próxima cabe
            raise BudgetExceeded
        calls[0] += 1
        prompt = (f"Pergunta: {question}\n\n"
                  f"Fontes:\n{_sources_text(item.get('source_ids', []), item.get('sources'))}\n\n"
                  f"Dados de ferramenta:\n{_tool_text(item.get('tool_calls'), item.get('evidence'))}\n\n"
                  f"Resposta: {answer}")
        return llm.complete(SYSTEM, [{"role": "user", "content": prompt}], max_tokens=4096, schema=SCHEMA).json()

    status, verdicts, pairs_by_split = "ran", [], defaultdict(list)
    calib = load_calibration(calibration)
    misses = []
    try:
        for c in calib:  # calibração primeiro: sem ela, a nota do juiz não tem valor
            got = judge_one(c, c["question"], c["answer"])["grounded"]
            pairs_by_split[c.get("split", "ALL")].append((got, c["human_grounded"]))
            if got != c["human_grounded"]:
                misses.append(c["id"])
        for path in inputs:
            for r in json.loads(path.read_text(encoding="utf-8"))["rows"]:
                if r.get("answer"):
                    verdicts.append({"id": r["id"], "split": r["split"], "source": path.name,
                                     # a linha diz se o juiz viu dado de ferramenta: sem ele, "não grounded"
                                     # de resposta com dado do CRM é cegueira do juiz, não alucinação
                                     "saw_tool_data": bool(r.get("tool_calls") or r.get("evidence")),
                                     "saw_tool_results": bool(r.get("evidence")),
                                     **judge_one(r, r["message"], r["answer"])})
    except BudgetExceeded:
        status = "aborted_max_usd"
    by_split = defaultdict(list)
    for v in verdicts:
        by_split[v["split"]].append(v)
    all_pairs = [p for ps in pairs_by_split.values() for p in ps]
    return {"status": status, "model": llm.model, "live_files": [p.name for p in inputs],
            "spent_usd": request_usage()["cost_usd"], "max_usd": max_usd,
            **_rates(verdicts), "by_split": {s: _rates(v) for s, v in sorted(by_split.items())},
            "calibration": {**_agreement(all_pairs), "misses": misses,
                            "by_split": {s: _agreement(p) for s, p in sorted(pairs_by_split.items()) if s != "ALL"}},
            "verdicts": verdicts}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", type=Path, help="arquivos live_*.json (padrão: todos em evals/results)")
    ap.add_argument("--max-usd", type=float, default=None)
    args = ap.parse_args(argv)
    from app.config import llm_provider
    from app.llm import LLMError, get_llm

    inputs = args.inputs or sorted(RESULTS_DIR.glob("live_*.json"))
    if llm_provider() != "anthropic":
        report = {"status": "skipped", "reason": "LLM_PROVIDER != anthropic (sem juiz no modo offline)",
                  "live_files": [p.name for p in inputs]}
    else:
        try:
            report = run(get_llm("judge"), inputs, max_usd=args.max_usd)
        except LLMError as exc:
            report = {"status": "error", "reason": str(exc), "live_files": [p.name for p in inputs]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "verdicts"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
