"""Analytics do atendimento: cada request vira dado (a16z, "Trading Margin for Moat", 04/06/2025: o
produto vence quando vira o "sistema de trabalho" que captura o dado, e o ciclo de feedback da linha de
frente chega ao produto sem telefone sem fio).

Resolvido automaticamente = respondeu sem handoff, sem bloqueio e sem número sem base. É a métrica de
negócio do atendimento; o 👍/👎 do cliente (POST /feedback) é a confirmação humana dela. GET /analytics
(operador) mostra as taxas, o custo por atendimento resolvido e as intenções que mais falham, e assim
diz onde o conteúdo ou o prompt precisam melhorar.
"""
from __future__ import annotations

from collections import defaultdict


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def _resolved(r: dict) -> bool:
    return not (r["handoff"] or r["blocked"] or r["ungrounded"])


def summarize(reqs: list[dict], feedback: list[dict]) -> dict:
    n = len(reqs)
    resolved = [r for r in reqs if _resolved(r)]
    cost = round(sum(r["cost_usd"] or 0.0 for r in reqs), 6)
    ms = [r["ms"] for r in reqs if r["ms"]]
    by_intent: dict[str, list[dict]] = defaultdict(list)
    for r in reqs:
        by_intent[r["intent"] or "unknown"].append(r)
    ratings = [f["rating"] for f in feedback]
    intents = {i: {"requests": len(rs), "resolution_rate": round(sum(map(_resolved, rs)) / len(rs), 3)}
               for i, rs in sorted(by_intent.items())}
    return {
        "requests": n,
        "resolution_rate": round(len(resolved) / n, 3) if n else None,
        "escalation_rate": round(sum(r["handoff"] for r in reqs) / n, 3) if n else None,
        "block_rate": round(sum(r["blocked"] for r in reqs) / n, 3) if n else None,
        "ungrounded_rate": round(sum(r["ungrounded"] for r in reqs) / n, 3) if n else None,
        "cost_usd_total": cost,
        "cost_usd_per_request": round(cost / n, 6) if n else None,
        "cost_usd_per_resolved": round(cost / len(resolved), 6) if resolved else None,
        "latency_ms": {"p50": _pct(ms, 0.5), "p95": _pct(ms, 0.95), "p99": _pct(ms, 0.99)},
        "feedback": {"ratings": len(ratings), "up": ratings.count(1), "down": ratings.count(-1),
                     "approval_rate": round(ratings.count(1) / len(ratings), 3) if ratings else None},
        "by_intent": intents,
        "worst_intents": sorted(intents, key=lambda i: (intents[i]["resolution_rate"], -intents[i]["requests"]))[:3],
    }
