"""Runner de avaliação: mede o sistema contra evals/cases.jsonl (+ heldout cego) e aplica o gate do CI.

Uso:
  python -m evals.run_evals                                # em processo, offline, determinístico (gate do CI)
  python -m evals.run_evals --url http://localhost:8000    # contra a API rodando (smoke do container)
  python -m evals.run_evals --split replan                 # só um split
  python -m evals.run_evals --live --split official --max-usd 1.5 --workers 4
        # ao vivo: LLM_PROVIDER=anthropic, para limpo ao atingir o teto de US$ e grava o parcial
        # em evals/results/live_<split>.json (live_all.json sem --split)
  python -m evals.run_evals --compare-retrievers           # Recall@3/MRR do BM25 e do híbrido
  python -m evals.run_evals --live --split official --max-usd 1   # ao vivo roda 3x por padrão: pass^3 e instáveis

Campos de `expect`: intent, handoff, blocked, language (conferido no TEXTO da resposta), tools (subconjunto
chamado), pending_action, replanned, not_contains, doc_ids (relevantes → Recall@3 e MRR), agents /
forbidden_agents (handoff por desfecho). `before`: mensagens anteriores na mesma conversa (casos de memória).

Isolamento: em processo, cada caso (e cada repetição) roda num banco SQLite próprio — memória de longo
prazo, tickets e ações não vazam entre casos, e o user_id (dono no CRM) continua o do caso. Toda taxa sai com
n e IC de Wilson a 95%.

Saída: offline grava evals/results/latest.json (fonte dos números do README via scripts/fill_readme.py).
Códigos: 0 ok · 1 gate reprovado (offline) · 2 sem credencial para --live · 3 abortado (teto de US$ ou
credencial recusada; o parcial fica gravado).
"""
from __future__ import annotations

import argparse
import contextvars
import json
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Importa o src/ DESTA pasta, não o pacote instalado: num worktree de fork o `pip install -e`
# aponta para a pasta principal e o eval mediria o código errado.
sys.path.insert(0, str(HERE.parent / "src"))
CASES = HERE / "cases.jsonl"
HELDOUT = HERE / "heldout_blind.jsonl"  # cego: commitado com a spec, antes de qualquer ajuste
RETRIEVAL = HERE / "retrieval.jsonl"
RESULTS_DIR = HERE / "results"
RESULTS = RESULTS_DIR / "latest.json"
# Splits medidos mas fora do gate: gatear o cego ou os difíceis convidaria a ajustar o sistema a eles.
UNGATED = {"heldout", "hard"}
# 0.9 é o piso usual de take-home; ajuste por desafio, nunca para "fazer passar".
GATE = float(os.getenv("EVAL_GATE", "0.9"))
K = 3  # Recall@3: o agente usa as 3 primeiras passagens
# Custo estimado por caso antes de haver medida na rodada. Smoke ao vivo de 31/10/2026 (11 casos,
# Haiku router + Sonnet supervisor/agente): média US$ 0,0093, máximo 0,013 → 0,012 dá folga sem travar
# o teto à toa. Depois da 1ª leva vale o maior entre isto e a média medida. Ajuste com --usd-per-case.
USD_PER_CASE = 0.012
# Ao vivo, cada caso roda 3 vezes por padrão: uma rodada só esconde o caso que acerta às vezes (pass^k).
LIVE_REPEAT = 3
# Piso de recusa dos negativos na calibração do portão: abaixo disso o agente afirma sem fonte com
# frequência demais. Acima dele, vence o limiar que mais responde (Getnet v3: acurácia balanceada com 14
# negativos deixou um único negativo empurrar o limiar a 14,938 e o handoff oficial caiu para 50%).
NEG_REFUSAL_MIN = 0.85
# Agente que passa pelo portão "sem fonte" (kit: faq; Getnet: knowledge). Negativo que o router manda para
# outro agente (clima/câmbio → general) não mede o portão e sai da calibração.
KNOWLEDGE_AGENTS = {"faq", "knowledge"}
ESCALATION_AGENT = "escalation"  # handoff por desfecho: o agente que leva o caso a um humano
Z95 = 1.959964  # quantil normal de 97,5%: IC bilateral de 95%

# Banco do caso corrente (contextvar: cada thread de caso vê o seu; as threads internas do pipeline e do
# supervisor copiam o contexto). None = banco do processo (DB_PATH).
_CASE_DB: contextvars.ContextVar[str | None] = contextvars.ContextVar("eval_case_db", default=None)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Intervalo de confiança de Wilson para k sucessos em n (fração 0..1). n = 0 → None.

    Wilson e não a aproximação normal: com n pequeno e taxa perto de 0 ou 100% (o caso típico de eval), o
    intervalo normal sai de [0, 1] ou colapsa em largura zero.
    """
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z / denom * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, center - half), min(1.0, center + half)


def rate_ci(k: int, n: int) -> dict | None:
    """{k, n, lo, hi} com lo/hi em PERCENTUAL (mesma escala dos *_acc), ou None sem amostra."""
    bounds = wilson(k, n)
    if bounds is None:
        return None
    return {"k": k, "n": n, "lo": round(100 * bounds[0], 1), "hi": round(100 * bounds[1], 1)}


def fmt_ci(ci: dict | None) -> str:
    return f"[IC95 {ci['lo']}–{ci['hi']}; n={ci['n']}]" if ci else "[n=0]"


# Palavras funcionais exclusivas de cada idioma (nenhuma existe nos dois: "a", "no", "do", "as" ficam de fora).
_PT_WORDS = frozenset(
    "de da das dos que para por com não nao é em na nas nos num numa um uma umas uns seu sua seus suas você voce"
    " vocês está estão são mais como mas ao aos à às ou foi já também pode posso ser tem têm isso esse essa"
    " este esta meu minha pelo pela quando onde qual quais sobre até aqui ainda há sem então pois deve".split())
_EN_WORDS = frozenset(
    "the and is are was were of to in it you your for with that this be being been have has can will not on at"
    " from by we our my an or if please there what when where which how would could should its about here"
    " any i'm i've we're you're don't can't won't couldn't".split())
_WORD = re.compile(r"[a-zà-öø-ÿ']+")


def detect_text_language(text: str) -> str | None:
    """Idioma do TEXTO por contagem de palavras funcionais pt/en; determinística e sem dependência.

    None = inconclusivo (menos de 2 palavras funcionais ou empate): a checagem de idioma fica de fora em vez
    de chutar. Getnet v3: conferir o campo declarado dava 100% com 4 de 89 respostas no idioma errado.
    """
    words = _WORD.findall((text or "").lower())
    pt = sum(w in _PT_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    if max(pt, en) < 2 or pt == en:
        return None
    return "pt" if pt > en else "en"


def load_cases(paths: tuple[Path, ...] | None = None) -> list[dict]:
    from app.jsonl import read_family

    paths = paths or (CASES, HELDOUT)  # lidos na chamada: os testes do kit apontam para o fixture

    cases = [c for path in paths for c in read_family(path)]  # base + <nome>.d/*.jsonl (um arquivo por fork)
    ids = [c["id"] for c in cases]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"ids duplicados nos casos: {sorted(dup)}")
    for c in cases:
        missing = {"id", "split", "message", "expect"} - c.keys()
        if missing:
            raise ValueError(f"caso {c.get('id', '?')} sem campos {sorted(missing)}")
    return cases


def has_credentials() -> bool:
    """Alguma credencial resolvível pelo SDK (chave, token, perfil do `ant auth login` ou WIF)."""
    names = ("APP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
             "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_IDENTITY_TOKEN_FILE")
    return any(os.getenv(n) for n in names) or (Path.home() / ".config" / "anthropic").is_dir()


class Client:
    def __init__(self, url: str | None) -> None:
        self.url = url.rstrip("/") if url else None

    def send(self, message: str, user_id: str, conversation_id: str | None) -> dict:
        from app.auth import issue_token
        from app.schemas import ChatRequest

        if self.url:
            import httpx
            r = httpx.post(f"{self.url}/chat", json={"message": message, "user_id": user_id,
                                                     "conversation_id": conversation_id},
                           headers={"Authorization": f"Bearer {issue_token(user_id)}"}, timeout=180)
            r.raise_for_status()
            return r.json()
        from app.pipeline import handle
        return handle(ChatRequest(message=message, conversation_id=conversation_id), user_id, True).model_dump()


def score(case: dict, got: dict) -> dict[str, bool]:
    exp = case["expect"]
    checks = {"intent": got.get("intent") == exp["intent"]}
    if "handoff" in exp:
        checks["handoff"] = (got.get("handoff") or {}).get("required", False) == exp["handoff"]
    # Handoff por desfecho (publicado AO LADO do flag, não no lugar): obrigatórios contidos nos agentes
    # chamados e nenhum proibido chamado; o caminho pode ter passos a mais. `handoff` vira obrigatório ou
    # proibido o agente de escalonamento.
    required, forbidden = set(exp.get("agents", [])), set(exp.get("forbidden_agents", []))
    if "handoff" in exp:
        (required if exp["handoff"] else forbidden).add(ESCALATION_AGENT)
    if required or forbidden:
        called = set(got.get("route", [])) - {"router", "guardrails"}
        checks["handoff_outcome"] = required <= called and not forbidden & called
    if "blocked" in exp:
        checks["blocked"] = got.get("blocked") == exp["blocked"]
    if "language" in exp:  # o TEXTO, não o campo declarado; inconclusivo fica fora da conta
        detected = detect_text_language(got.get("answer", ""))
        if detected is not None:
            checks["language"] = detected == exp["language"]
    if "tools" in exp:
        called = {c["name"] for c in got.get("tool_calls", [])}
        checks["tools"] = set(exp["tools"]) <= called
    if "pending_action" in exp:
        checks["pending_action"] = bool(got.get("pending_action")) == exp["pending_action"]
    if "replanned" in exp:
        checks["replanned"] = bool(got.get("replans")) == exp["replanned"]
    if got.get("answer") and not got.get("blocked"):  # número sem base em fonte/tool/pergunta (app/grounding.py)
        checks["numbers"] = not got.get("unsupported_numbers")
    if "not_contains" in exp:
        blob = json.dumps(got, ensure_ascii=False)
        checks["no_leak"] = not any(s in blob for s in exp["not_contains"])
    return checks


def _install_case_db():
    """Faz `app.store.get_store()` respeitar o banco do caso corrente (_CASE_DB). Devolve o original.

    Defesa do lado do eval: o store lê DB_PATH do ambiente, que é do processo inteiro e não serve para
    casos em threads paralelas. Se o store mudar e o gancho não pegar, run_case quebra alto (crash no
    relatório), em vez de medir com memória compartilhada em silêncio.
    """
    from app import store

    original = store.db_path

    def db_path() -> Path:
        override = _CASE_DB.get()
        return Path(override) if override else original()

    store.db_path = db_path
    return original


def _case_store(db: str):
    """Cria (e põe no cache) o Store do caso na thread que chama: o main, antes de abrir os workers."""
    from app.store import get_store

    token = _CASE_DB.set(db)
    try:
        store = get_store()
    finally:
        _CASE_DB.reset(token)
    if Path(store.path) != Path(db):
        raise RuntimeError(f"isolamento do eval falhou: get_store() devolveu {store.path}, esperado {db}")
    return store


def run_case(client, case: dict, db: str | None = None) -> dict:
    from app.rag import recall_at_k, reciprocal_rank

    token = _CASE_DB.set(db) if db else None
    t0 = time.perf_counter()
    try:
        if db:  # o gancho precisa valer AQUI, na thread do caso; senão é crash, não medida contaminada
            from app.store import get_store
            if Path(get_store().path) != Path(db):
                raise RuntimeError(f"isolamento do eval falhou: banco {get_store().path}, esperado {db}")
        conv, user = None, case.get("user_id", "u1")
        for prior in case.get("before", []):
            conv = client.send(prior, user, conv)["conversation_id"]
        got, crash = client.send(case["message"], user, conv), None
    except Exception as exc:  # crash vira métrica, não derruba o relatório
        got, crash = {}, f"{type(exc).__name__}: {exc}"
    finally:
        if token is not None:
            _CASE_DB.reset(token)
    ms = (time.perf_counter() - t0) * 1000
    row = {"id": case["id"], "split": case["split"], "message": case["message"], "ms": round(ms, 1),
           "expected_intent": case["expect"].get("intent"),
           "checks": score(case, got) if not crash else {"intent": False}, "crash": crash,
           "got_intent": got.get("intent"), "route": got.get("route", []), "replans": got.get("replans", []),
           "engine": got.get("engine"), "answer": got.get("answer", ""),
           "source_ids": [s["id"] for s in got.get("sources", [])],
           # o que a resposta usou, para o juiz: fontes com título/URL (web inclusive), tools chamadas e, quando
           # o contrato da API trouxer, o texto da evidência (resultados de tool). Ausente = [] declarado.
           "sources": [{k: s.get(k) for k in ("id", "title", "url")} for s in got.get("sources", [])],
           "tool_calls": [{"name": c.get("name"), "args": c.get("args", {}), "ok": c.get("ok", True)}
                          for c in got.get("tool_calls", [])],
           "evidence": list(got.get("evidence") or []),
           "expected_language": case["expect"].get("language"), "language_declared": got.get("language"),
           "language_detected": detect_text_language(got.get("answer", "")),
           "unsupported_numbers": got.get("unsupported_numbers", []),
           "cost_usd": (got.get("usage") or {}).get("cost_usd", 0)}
    relevant = case["expect"].get("doc_ids")
    if relevant:
        row["recall_at_k"] = recall_at_k(row["source_ids"], relevant, K)
        row["rr"] = reciprocal_rank(row["source_ids"], relevant)
    return row


def _llm_errors(kind: str | None = None) -> float:
    from app.observability import METRICS

    return sum(v for (name, labels), v in list(METRICS.counters.items())
               if name == "llm_errors_total" and (kind is None or ("kind", kind) in labels))


def summarize(rows: list[dict]) -> dict:
    by_split: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_split[r["split"]].append(r)
        by_split["ALL"].append(r)

    checks = ("intent", "handoff", "handoff_outcome", "blocked", "language", "tools", "pending_action",
              "replanned", "no_leak", "numbers")

    def vals(items: list[dict], key: str) -> list[bool]:
        return [r["checks"][key] for r in items if key in r["checks"]]

    def rate(items: list[dict], key: str) -> float | None:
        v = vals(items, key)
        return round(100 * sum(v) / len(v), 1) if v else None

    def mean(items: list[dict], key: str) -> float | None:
        v = [r[key] for r in items if key in r]
        return round(sum(v) / len(v), 3) if v else None

    metrics = {}
    for split, items in by_split.items():
        lat = sorted(r["ms"] for r in items)
        metrics[split] = {
            "cases": len(items),
            **{f"{k}_acc": rate(items, k) for k in checks},
            # respostas sem idioma detectável no texto (curtas demais): fora do language_acc, mas contadas
            "language_undetected": sum(1 for r in items if r.get("expected_language") and r.get("answer")
                                       and r.get("language_detected") is None),
            f"recall@{K}": mean(items, "recall_at_k"),
            "mrr": mean(items, "rr"),
            "p50_ms": lat[len(lat) // 2] if lat else None,
            "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
            "cost_usd": round(sum(r["cost_usd"] for r in items), 6),
            "crashes": sum(1 for r in items if r["crash"]),
            # n e IC de Wilson de cada taxa *_acc (lo/hi em %, mesma escala)
            "ci": {f"{k}_acc": rate_ci(sum(vals(items, k)), len(vals(items, k))) for k in checks
                   if vals(items, k)},
        }
    return metrics


def run(url: str | None = None, out: Path | None = None, split: str | None = None, live: bool = False,
        max_usd: float | None = None, workers: int = 1, client=None, compare: bool = False,
        embedder=None, usd_per_case: float = USD_PER_CASE, repeat: int = 1, rerank_llm=None) -> dict:
    saved = {k: os.environ.get(k) for k in ("DB_PATH", "LLM_PROVIDER")}
    original_db_path, case_dir = None, None
    try:
        if not url:  # banco descartável: o eval não herda conversas de execuções anteriores
            case_dir = Path(tempfile.mkdtemp(prefix="eval-"))
            os.environ["DB_PATH"] = str(case_dir / "eval.db")
            from app.store import get_store

            # Store aquecido no main ANTES da 1ª leva: criado dentro dos workers, a v3 abriu com
            # "database is locked" nos primeiros casos (31/10/2026). A trava no store.py é a outra defesa.
            get_store()
            original_db_path = _install_case_db()
        if live and not url:
            os.environ["LLM_PROVIDER"] = "anthropic"
        if live and not url and client is None:
            from app.llm import warmup

            warmup()  # o import do SDK fica fora do tempo do 1º caso (smoke de 31/10/2026: ~7 s)
        client = client or Client(url)
        cases = [c for c in load_cases() if not split or c["split"] == split]
        repeat = max(1, repeat)
        todo = [c for _ in range(repeat) for c in cases]  # rodada a rodada: a 2ª só começa depois da 1ª
        rows: list[dict] = []
        status, spent, auth_before = "complete", 0.0, _llm_errors("auth")
        start = 0
        while start < len(todo):
            size = min(max(1, workers), len(todo) - start)
            if max_usd is not None:  # projeta a leva: lançar e só depois somar estourava o teto
                est = max(usd_per_case, spent / len(rows) if rows else 0.0)
                size = min(size, int((max_usd - spent) / est + 1e-9))
                if size <= 0:
                    status = "aborted_max_usd"
                    break
            wave = todo[start:start + size]
            runs_of_wave = [(start + i) // max(1, len(cases)) for i in range(size)]
            # um banco por caso E por repetição (memória de longo prazo não vaza entre casos nem rodadas),
            # criado aqui no main antes de abrir os workers
            dbs = [str(case_dir / f"case-{start + i:05d}.db") if case_dir else None for i in range(size)]
            for db in dbs:
                if db:
                    _case_store(db)
            start += size
            if len(wave) == 1:
                done = [run_case(client, wave[0], dbs[0])]
            else:  # cada caso no seu contexto (trace_id/custo por request); ordem dos casos preservada
                with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                    futures = [pool.submit(contextvars.copy_context().run, run_case, client, c, db)
                               for c, db in zip(wave, dbs, strict=True)]
                    done = [f.result() for f in futures]
            for row, run_i in zip(done, runs_of_wave, strict=True):
                row["run"] = run_i
            rows += done
            spent = round(spent + sum(r["cost_usd"] for r in done), 6)
            if live and _llm_errors("auth") > auth_before:
                status = "aborted_auth"  # chave recusada: o resto cairia no offline e mediria a coisa errada
                break
        metrics = summarize(rows) if rows else {"ALL": {"cases": 0, "intent_acc": None, "crashes": 0}}
        gated = [r for r in rows if r["split"] not in UNGATED]
        vals = [r["checks"]["intent"] for r in gated]
        overall = sum(vals) / len(vals) if vals else 0.0
        threshold = None
        if not url:  # o limiar que o agente USA neste processo (env > constante), não o padrão do código
            from app.agents import MIN_SCORE
            from app.config import coverage_min_score

            threshold = {"effective": coverage_min_score(MIN_SCORE), "source": _threshold_source()}
        provider = "remote (ver /health do servidor)" if url else os.getenv("LLM_PROVIDER", "offline")
        report = {"mode": "http" if url else "in-process", "provider": provider,
                  "live": live, "split": split or "all", "status": status, "spent_usd": spent,
                  "max_usd": max_usd, "llm_errors": _llm_errors(), "gate": GATE, "intent_acc": round(overall, 4),
                  "intent_ci": rate_ci(sum(vals), len(vals)),
                  # db_per_case: cada caso no seu banco; http: o servidor decide (memória por user_id compartilhada)
                  "isolation": "db_per_case" if not url else "shared_http",
                  "coverage_min_score": threshold,
                  "gated_splits": sorted({r["split"] for r in gated}), "ungated_splits": sorted(UNGATED),
                  "passed": bool(rows) and overall >= GATE and metrics["ALL"]["crashes"] == 0,
                  "metrics": metrics, "rows": rows,
                  "failures": [r for r in rows if r["crash"] or not all(r["checks"].values())],
                  "stability": stability(rows, repeat),
                  "failure_matrix": failure_matrix(rows),
                  "gate_calibration": calibrate_gate() if not url else None,
                  "retrieval": retrieval_metrics()}
        if compare:
            report["retrieval_compare"] = compare_retrievers(embedder=embedder, rerank_llm=rerank_llm)
        target = out or (RESULTS_DIR / f"live_{split or 'all'}.json" if live else RESULTS)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        report["path"] = str(target)
        return report
    finally:
        if original_db_path is not None:
            from app import store

            store.db_path = original_db_path
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def stability(rows: list[dict], runs: int) -> dict:
    """Confiabilidade com --repeat: pass@1 (média por execução), pass^k (caso passa em TODAS) e instáveis.

    Anthropic, "Demystifying evals for AI agents" (09/01/2026): 75% por tentativa dá pass^3 ≈ 42%. Agente de
    atendimento precisa acertar sempre; uma rodada só esconde o caso que acerta às vezes.
    """
    by_case: dict[str, dict] = {}
    for r in rows:
        c = by_case.setdefault(r["id"], {"passes": 0, "runs": 0})
        c["runs"] += 1
        c["passes"] += int(not r["crash"] and all(r["checks"].values()))
    n = len(by_case) or 1
    passes = sum(c["passes"] for c in by_case.values())
    always = sum(c["passes"] == c["runs"] for c in by_case.values())
    return {"runs": runs,
            "pass_at_1": round(passes / (len(rows) or 1), 4),
            "pass_hat_k": round(always / n, 4),
            # pass@1 conta execuções (repetições do mesmo caso não são independentes: o IC sai otimista);
            # pass^k conta casos
            "ci": {"pass_at_1": rate_ci(passes, len(rows)), "pass_hat_k": rate_ci(always, len(by_case))},
            "unstable": sorted(i for i, c in by_case.items() if 0 < c["passes"] < c["runs"]),
            "by_case": by_case}


def retrieval_metrics(path: Path | None = None, retriever=None) -> dict | None:
    """Recall@3, MRR e hit@3 sobre perguntas rotuladas olhando a fonte, independentes do router."""
    from app.jsonl import family, read_family

    path = path or RETRIEVAL  # lido na chamada: o conftest troca pela fixture do kit
    if not family(path):
        return None
    from app.config import retriever_name
    from app.rag import get_retriever, recall_at_k, reciprocal_rank

    retriever = retriever or get_retriever(rerank=False)  # mede o base: rerank só no comparativo (custo fora do teto)
    all_rows = read_family(path)
    rows = [r for r in all_rows if r["relevant"]]  # negativos (sem fonte) medem o portão, não o recall
    rec, rr, hit = [], [], []
    for r in rows:
        got = [h.doc.id for h in retriever.search(r["question"], k=K)]
        rec.append(recall_at_k(got, r["relevant"], K))
        rr.append(reciprocal_rank(got, r["relevant"]))
        hit.append(bool(set(got) & set(r["relevant"])))
    n = len(rows) or 1
    # hit@3 é taxa (acertou ou não) → IC de Wilson; Recall@3 e MRR são médias de frações, sem Wilson
    return {"cases": len(rows), "positives": len(rows), "negatives": len(all_rows) - len(rows),
            f"recall@{K}": round(sum(rec) / n, 3), "mrr": round(sum(rr) / n, 3),
            f"hit@{K}": round(sum(hit) / n, 3), f"hit@{K}_ci": rate_ci(sum(hit), len(rows)),
            "retriever": retriever.name, "requested": retriever_name()}


def _threshold_source() -> str:
    return "COVERAGE_MIN_SCORE" if os.getenv("COVERAGE_MIN_SCORE") else "MIN_SCORE"


def _reaches_knowledge(question: str) -> bool:
    """O router (regras offline: determinístico e sem custo) manda esta pergunta ao agente de conhecimento?"""
    from app.router import classify_offline

    return bool(KNOWLEDGE_AGENTS & set(classify_offline(question, []).plan))


def calibrate_gate(path: Path | None = None, retriever=None) -> dict | None:
    """Escolhe o limiar do portão "sem fonte" por medida (feedback v2: o portão chutado barrava 1 de 5).

    Positivos (com `relevant`) devem ser respondidos COM a fonte certa entre os trechos aceitos; negativos
    (`relevant: []`) devem ser recusados. Usa a mesma função do agente (agents.covered_hits).
    Melhor limiar = o que mais responde positivos com recusa de negativos >= NEG_REFUSAL_MIN; empate → o
    MENOR (responde mais perto da fronteira sem perder o piso). Nenhum atinge o piso → o que mais recusa.
    Negativo que o router não manda ao agente de conhecimento sai da conta (`excluded_negatives`).
    `current` = limiar efetivo (COVERAGE_MIN_SCORE do ambiente, senão MIN_SCORE), com o que ele entrega.
    """
    from app.jsonl import family, read_family

    path = path or RETRIEVAL
    if not family(path):
        return None
    from app.agents import MIN_SCORE, covered_hits
    from app.config import coverage_min_score
    from app.rag import get_retriever

    current = coverage_min_score(MIN_SCORE)
    rows = read_family(path)
    pos = [r for r in rows if r["relevant"]]
    all_neg = [r for r in rows if not r["relevant"]]
    neg = [r for r in all_neg if _reaches_knowledge(r["question"])]
    excluded = [r["question"] for r in all_neg if r not in neg]
    if not pos or not neg:
        return {"status": "needs_negatives", "positives": len(pos), "negatives": len(neg),
                "excluded_negatives": excluded, "current": current, "current_source": _threshold_source()}
    retriever = retriever or get_retriever(rerank=False)  # mede o base: rerank só no comparativo (custo fora do teto)
    found = {r["question"]: retriever.search(r["question"], k=K) for r in pos + neg}
    candidates = sorted({round(h.bm25, 3) for hits in found.values() for h in hits} | {MIN_SCORE, 0.0, current})
    table = []
    for t in candidates:
        ok_pos = sum(bool({h.doc.id for h in covered_hits(found[r["question"]], t)} & set(r["relevant"])) for r in pos)
        ok_neg = sum(not covered_hits(found[r["question"]], t) for r in neg)
        a, b = ok_pos / len(pos), ok_neg / len(neg)
        table.append({"threshold": t, "pos_answered": round(a, 3), "neg_refused": round(b, 3),
                      "balanced_acc": round((a + b) / 2, 3), "meets_floor": b >= NEG_REFUSAL_MIN - 1e-12,
                      "pos_ci": rate_ci(ok_pos, len(pos)), "neg_ci": rate_ci(ok_neg, len(neg))})
    ok = [row for row in table if row["meets_floor"]]
    if ok:
        best = max(ok, key=lambda row: (row["pos_answered"], -row["threshold"]))
    else:
        best = max(table, key=lambda row: (row["neg_refused"], row["pos_answered"], -row["threshold"]))
    at_current = next(row for row in table if row["threshold"] == current)
    return {"positives": len(pos), "negatives": len(neg), "excluded_negatives": excluded,
            "criterion": f"max pos_answered com neg_refused >= {NEG_REFUSAL_MIN}; empate → menor limiar",
            "neg_refusal_min": NEG_REFUSAL_MIN, "current": current, "current_source": _threshold_source(),
            "at_current": at_current, "best": best, "table": table, "retriever": retriever.name}


def failure_matrix(rows: list[dict]) -> dict:
    """Taxonomia de falhas (Hamel Husain, Evals FAQ, 21/09/2026): onde cada checagem falha, por split, e com
    que intenção o router confunde a esperada. Diz o que corrigir primeiro sem ler caso a caso."""
    by_check: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # acertos E erros, por intenção
    for r in rows:
        if r.get("expected_intent") and "intent" in r["checks"]:
            matrix[r["expected_intent"]][r.get("got_intent") or "crash"] += 1
        for check, ok in r["checks"].items():
            if not ok:
                by_check[check][r["split"]] += 1
        if not r["checks"].get("intent", True) and r.get("expected_intent"):
            confusion[r["expected_intent"]][r.get("got_intent") or "crash"] += 1
    return {"by_check": {k: dict(v) for k, v in by_check.items()},
            "intent_confusion": {k: dict(v) for k, v in confusion.items()},
            "intent_matrix": {k: dict(v) for k, v in matrix.items()}}


def _rerank_llm():
    from app.llm import get_llm

    return get_llm("reranker")  # offline: LLMError → cada pergunta cai na ordem do base (linha = bm25)


def compare_retrievers(path: Path | None = None, embedder=None, rerank_llm=None) -> dict:
    """Mede BM25 e híbrido sobre o mesmo retrieval.jsonl. Híbrido sem fastembed = 'unavailable', não erro.

    Com `rerank_llm` (CLI: --rerank, que usa o papel "reranker"), soma a linha "bm25+rerank": é a única medida que
    chama LLM aqui, e só quando pedida. Kit 3.5.0, corpus real da v3: R@3 0,708 → 0,811.
    """
    path = path or RETRIEVAL
    from app.rag import BM25Retriever, FastEmbedEmbedder, HybridRetriever, RerankRetriever, get_index

    out = {"bm25": retrieval_metrics(path, BM25Retriever(get_index()))}
    if rerank_llm is not None:
        out["bm25+rerank"] = retrieval_metrics(path, RerankRetriever(BM25Retriever(get_index()), llm=rerank_llm))
    try:
        out["hybrid"] = retrieval_metrics(path, HybridRetriever(get_index(), embedder or FastEmbedEmbedder()))
        out["hybrid"]["embedder"] = getattr(embedder, "name", None) or "fastembed"
    except Exception as exc:  # sem o extra [hybrid] ou sem rede para baixar o modelo
        out["hybrid"] = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def _retrieval_line(ret: dict) -> str:
    if ret.get("status") == "unavailable":
        return f"indisponível ({ret['reason']})"
    return (f"Recall@3 **{ret['recall@3']}** · MRR **{ret['mrr']}** · hit@3 {ret['hit@3']} "
            f"{fmt_ci(ret.get('hit@3_ci'))} ({ret['cases']} perguntas)")


def fmt_cell(metrics: dict, key: str) -> str:
    value = metrics.get(key)
    if value is None:
        return "—"
    ci = (metrics.get("ci") or {}).get(key)
    return f"{value} {fmt_ci(ci)}" if key.endswith("_acc") and ci else str(value)


def intent_matrix_table(matrix: dict[str, dict[str, int]]) -> str:
    """Matriz de confusão do router (linha = esperada, coluna = obtida; diagonal em negrito) + acerto por linha.
    Espelha a evidência mais barata e mais lida do Agent Swarm público (EikiYamashiro): onde o router erra, à vista."""
    rows = sorted(matrix)
    cols = sorted({g for m in matrix.values() for g in m} | set(rows))
    head = "| esperada \\ obtida | " + " | ".join(cols) + " | acerto |"
    lines = [head, "|---|" + "---:|" * (len(cols) + 1)]
    for exp in rows:
        total = sum(matrix[exp].values())
        cells = [(f"**{matrix[exp].get(c, 0)}**" if c == exp else str(matrix[exp].get(c, 0) or "·")) for c in cols]
        lines.append(f"| {exp} | " + " | ".join(cells) + f" | {matrix[exp].get(exp, 0) / total:.0%} |")
    return "Matriz de confusão do router (todos os splits):\n\n" + "\n".join(lines)


def to_markdown(report: dict) -> str:
    splits = [s for s in report["metrics"] if s != "ALL"] + ["ALL"]
    keys = [k for k in report["metrics"]["ALL"] if k != "ci"]
    out = ["| Métrica | " + " | ".join(splits) + " |", "|---|" + "---:|" * len(splits)]
    for k in keys:
        out.append(f"| {k} | " + " | ".join(fmt_cell(report["metrics"][s], k) for s in splits) + " |")
    verdict = "PASS" if report["passed"] else "FAIL"
    out.append(f"\nGate (splits {', '.join(report.get('gated_splits', []))}): intent {report['intent_acc']:.1%} "
               f"{fmt_ci(report.get('intent_ci'))} (mínimo {report['gate']:.0%}) → **{verdict}**. "
               f"Medidos fora do gate: {', '.join(report.get('ungated_splits', []))}. "
               f"Isolamento: `{report.get('isolation', '?')}`.")
    fm = report.get("failure_matrix") or {}
    if fm.get("by_check"):
        out.append("\nFalhas por checagem × split: " + " · ".join(
            f"{check} " + ", ".join(f"{sp} {n}" for sp, n in sorted(splits.items()))
            for check, splits in sorted(fm["by_check"].items())))
    if fm.get("intent_confusion"):
        out.append("Confusão de intenção (esperada → obtida): " + " · ".join(
            f"{exp} → " + ", ".join(f"{got} {n}" for got, n in got_map.items())
            for exp, got_map in sorted(fm["intent_confusion"].items())))
    if fm.get("intent_matrix"):
        out.append("\n" + intent_matrix_table(fm["intent_matrix"]))
    gc = report.get("gate_calibration") or {}
    thr = report.get("coverage_min_score")
    if thr or gc.get("best"):
        line = "\nPortão sem fonte:"
        if thr:
            line += f" limiar efetivo {thr['effective']} ({thr['source']})"
        cur = gc.get("at_current")
        if cur:
            line += (f" → responde {cur['pos_answered']:.1%} {fmt_ci(cur.get('pos_ci'))} dos com fonte, "
                     f"recusa {cur['neg_refused']:.1%} {fmt_ci(cur.get('neg_ci'))} dos sem fonte")
        if gc.get("best"):
            b = gc["best"]
            excl = len(gc.get("excluded_negatives", []))
            line += (f" · recomendado {b['threshold']} → responde {b['pos_answered']:.1%} {fmt_ci(b.get('pos_ci'))}, "
                     f"recusa {b['neg_refused']:.1%} {fmt_ci(b.get('neg_ci'))} ({gc['positives']} com fonte, "
                     f"{gc['negatives']} sem; {excl} sem fonte fora da conta por não chegarem ao agente de "
                     f"conhecimento; critério: {gc.get('criterion', 'acurácia balanceada')})")
        out.append(line)
    st = report.get("stability") or {}
    if st.get("runs", 1) > 1:
        ci = st.get("ci") or {}
        out.append(f"\nEstabilidade ({st['runs']} rodadas): pass@1 {st['pass_at_1']:.1%} {fmt_ci(ci.get('pass_at_1'))}"
                   f" · pass^{st['runs']} {st['pass_hat_k']:.1%} {fmt_ci(ci.get('pass_hat_k'))} · "
                   f"instáveis: {', '.join(st['unstable']) or '—'}")
    if report.get("live"):
        out.append(f"\nAo vivo: status `{report['status']}` · gasto US$ {report['spent_usd']} "
                   f"(teto {report['max_usd']}) · erros de LLM {report['llm_errors']:g}")
    ret = report.get("retrieval")
    if ret:
        out.append(f"\nRetrieval ativo ({ret['retriever']}, `evals/retrieval.jsonl`): {_retrieval_line(ret)}")
    for name, cmp in (report.get("retrieval_compare") or {}).items():
        out.append(f"- {name}: {_retrieval_line(cmp)}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # console cp1252 do Windows quebra acentos
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="avaliar a API rodando em vez do pipeline em processo")
    ap.add_argument("--out", type=Path, help="arquivo de saída (padrão latest.json ou live_<split>.json)")
    ap.add_argument("--split", help="roda só um split (ex.: replan)")
    ap.add_argument("--live", action="store_true", help="LLM real (LLM_PROVIDER=anthropic)")
    ap.add_argument("--max-usd", type=float, default=None, help="teto de gasto; para limpo e grava o parcial")
    ap.add_argument("--workers", type=int, default=1, help="casos em paralelo (ao vivo: corta o tempo de parede)")
    ap.add_argument("--compare-retrievers", action="store_true", help="mede BM25 e híbrido")
    ap.add_argument("--rerank", action="store_true",
                    help="com --compare-retrievers e LLM real: soma a linha bm25+rerank (Haiku; centavos)")
    ap.add_argument("--calibrate-gate", action="store_true", help="tabela do limiar do portão sem fonte e sai")
    ap.add_argument("--ablate-replan", action="store_true",
                    help="mede o split replan SEM replanejamento (plano fixo do router); grava ablation_replan*.json")
    ap.add_argument("--repeat", type=int, default=None,
                    help=f"roda cada caso N vezes: pass^k e casos instáveis (padrão {LIVE_REPEAT} no ao vivo, "
                         "1 offline)")
    ap.add_argument("--usd-per-case", type=float, default=USD_PER_CASE,
                    help="custo estimado por caso para projetar o teto antes da 1ª medida")
    args = ap.parse_args(argv)
    if args.calibrate_gate:
        cal = calibrate_gate()
        if not cal or not cal.get("table"):
            print(f"Precisa de perguntas com e sem fonte em {RETRIEVAL.name}: {cal}")
            return 1
        print(f"Critério: {cal['criterion']}. {cal['positives']} com fonte, {cal['negatives']} sem "
              f"({len(cal['excluded_negatives'])} sem fonte fora da conta: o router não os manda ao agente de "
              "conhecimento).\n")
        print("| limiar | responde (com fonte) | recusa (sem fonte) | piso | acurácia balanceada |\n"
              "|---:|---:|---:|:-:|---:|")
        for row in cal["table"]:
            mark = (" ← melhor" if row is cal["best"] else "") + (" ← em uso" if row is cal["at_current"] else "")
            print(f"| {row['threshold']} | {row['pos_answered']:.0%} {fmt_ci(row['pos_ci'])} | "
                  f"{row['neg_refused']:.0%} {fmt_ci(row['neg_ci'])} | {'ok' if row['meets_floor'] else '—'} | "
                  f"{row['balanced_acc']:.0%}{mark} |")
        print(f"\nEm uso: {cal['current']} ({cal['current_source']}). Para adotar: "
              f"COVERAGE_MIN_SCORE={cal['best']['threshold']} no .env e no compose.")
        return 0
    if args.live and not args.url and not has_credentials():
        print("SEM CREDENCIAL: --live precisa de APP_ANTHROPIC_API_KEY (nuvem) ou ANTHROPIC_API_KEY (.env local)"
              " ou de `ant auth login`. Nada foi rodado.")
        return 2
    repeat = args.repeat if args.repeat is not None else (LIVE_REPEAT if args.live else 1)
    if args.ablate_replan:  # mesma rodada, supervisor sem desvio: o README mostra com × sem lado a lado
        os.environ["SUPERVISOR_REPLAN"] = "off"
        args.split = args.split or "replan"
        args.out = args.out or RESULTS_DIR / ("ablation_replan_live.json" if args.live else "ablation_replan.json")
    report = run(args.url, args.out, args.split, args.live, args.max_usd, args.workers,
                 compare=args.compare_retrievers, usd_per_case=args.usd_per_case, repeat=repeat,
                 rerank_llm=_rerank_llm() if args.rerank else None)
    print(to_markdown(report))
    print(f"\nGravado em {report['path']}")
    for f in report["failures"]:
        print(f"- {f['id']}: got intent={f['got_intent']} checks={f['checks']} crash={f['crash']}")
    if report["status"] != "complete":
        print(f"ABORTADO ({report['status']}): parcial com {len(report['rows'])} caso(s) gravado.")
        return 3
    if args.live or args.ablate_replan:
        return 0  # ao vivo e ablação são evidência, não gate (a ablação falha de propósito): o gate é o offline
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
