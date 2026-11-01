"""Supervisor: executa o plano do router, observa o status de cada agente e replaneja.

Dois motores com a mesma saída (Outcome):
- llm (papel "supervisor": Sonnet 5.5, effort low): loop manual de tool use em que os especialistas
  são tools (faq_agent, records_agent, escalation_agent). O modelo lê cada status (ok|insufficient|error)
  e decide o próximo passo. Várias tool calls na mesma resposta rodam em paralelo.
- table (offline/CI e fallback de qualquer LLMError): o mesmo laço com transições determinísticas
  (PLANS, FALLBACK, ALLOWED, MAX_STEPS). Passos independentes do plano rodam em paralelo.

Paralelismo: threads (os agentes fazem I/O — SQLite, HTTP do LLM). Cada thread recebe uma cópia do
contextvars (trace_id, custo do request, span OTel pai). O resultado entra SEMPRE na ordem do plano
(ou dos blocos tool_use), nunca na ordem de término: rota, resposta e eval ficam determinísticos.
Todo desvio do plano vai para `replans`.
"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import domain
from .agents import AGENTS, AgentResult, Context
from .config import supervisor_parallel, supervisor_replan
from .llm import LLMError, get_llm
from .observability import METRICS, span

# PLANS, FALLBACK, ALLOWED, DEPENDS e NO_ESCALATION são do DOMÍNIO (app/domain/<ativo>.py), lidos em tempo de
# chamada: o desafio nunca edita este arquivo. `supervisor.PLANS` continua funcionando (ver __getattr__).
_DOMAIN_NAMES = ("PLANS", "FALLBACK", "ALLOWED", "DEPENDS", "NO_ESCALATION")


def __getattr__(attr: str):
    if attr in _DOMAIN_NAMES:
        return getattr(domain.active(), attr)
    raise AttributeError(attr)


def _d():
    return domain.active()


SEQUENTIAL = {"escalation"}  # sempre sozinho e depois dos outros: lê o motivo de quem falhou
MAX_STEPS = 4  # teto de chamadas de agente por turno (plano de 2 + replan + escalonamento)
LOW_CONFIDENCE = 0.4
TOOLS = [
    {"name": "faq_agent", "strict": True,
     "description": "Responde dúvidas sobre produto e políticas com RAG e fonte citada. "
                    "Devolve {status, text, sources}.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["query"],
                      "properties": {"query": {"type": "string", "description": "Pergunta + fatos já obtidos"}}}},
    {"name": "records_agent", "strict": True,
     "description": "Lê registros do PRÓPRIO usuário autenticado e propõe ações que exigem confirmação.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["request"],
                      "properties": {"request": {"type": "string"}}}},
    {"name": "escalation_agent", "strict": True,
     "description": "Abre caso para atendente humano. Use em pedido explícito, sem fonte, falha repetida ou risco.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["reason"],
                      "properties": {"reason": {"type": "string"}}}},
]
_TOOL_TO_AGENT = {"faq_agent": "faq", "records_agent": "records", "escalation_agent": "escalation"}


@dataclass
class Outcome:
    results: list[AgentResult] = field(default_factory=list)
    route: list[str] = field(default_factory=list)
    replans: list[str] = field(default_factory=list)
    answer: str | None = None  # resposta final redigida pelo supervisor LLM (None = juntar textos dos agentes)
    engine: str = "table"


def _invoke(name: str, ctx: Context) -> AgentResult:
    with span("agent", agent=name) as fields:
        try:
            result = AGENTS[name](ctx)
        except Exception as exc:  # agente quebrado vira status "error": o supervisor replaneja
            result = AgentResult(name, "error", reason=f"exception:{type(exc).__name__}")
        fields.update(status=result.status, reason=result.reason)  # quem desistiu e por quê, no log
        return result


def run_batch(names: list[str], ctx: Context) -> list[AgentResult]:
    """Executa os agentes; em paralelo quando há mais de um. Devolve na ordem de `names`."""
    if len(names) == 1 or not supervisor_parallel():
        return [_invoke(n, ctx) for n in names]
    METRICS.inc("parallel_batches_total", size=str(len(names)))
    with span("parallel", agents=",".join(names)), ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, _invoke, n, ctx) for n in names]
        return [f.result() for f in futures]


def _record(name: str, result: AgentResult, ctx: Context, out: Outcome) -> None:
    out.route.append(name)
    out.results.append(result)
    for key, value in result.facts.items():  # fatos acumulam entre agentes, na ordem do plano
        if isinstance(value, dict) and isinstance(ctx.facts.get(key), dict):
            ctx.facts[key].update(value)
        elif isinstance(value, list) and isinstance(ctx.facts.get(key), list):
            ctx.facts[key].extend(value)
        else:
            ctx.facts[key] = value


def next_batch(queue: list[str], budget: int) -> list[str]:
    """Primeiro da fila + os seguintes que não dependem de ninguém do lote. Remove-os da fila."""
    batch = [queue.pop(0)]
    while queue and len(batch) < budget and batch[0] not in SEQUENTIAL and queue[0] not in SEQUENTIAL \
            and not (_d().DEPENDS.get(queue[0], set()) & set(batch)):
        batch.append(queue.pop(0))
    return batch


def next_step(result: AgentResult, done: list[str], intent: str | None = None) -> str | None:
    if not supervisor_replan():  # ablação: plano fixo, falha não desvia
        return None
    d = _d()
    nxt = d.FALLBACK.get(result.agent, "escalation")
    if nxt == "escalation" and intent in d.NO_ESCALATION:
        return None
    return None if nxt in done or nxt not in d.ALLOWED.get(result.agent, set()) else nxt


def run_table(plan: list[str], ctx: Context, confidence: float = 1.0) -> Outcome:
    out = Outcome()
    queue = list(plan)
    if confidence < LOW_CONFIDENCE and queue and "escalation" not in queue \
            and ctx.intent not in _d().NO_ESCALATION and supervisor_replan():
        queue.append("escalation")
        ctx.handoff_reason = "low_confidence"
        out.replans.append(f"router:low_confidence({confidence:.2f})->escalation")
    while queue and len(out.route) < MAX_STEPS:
        batch = next_batch(queue, MAX_STEPS - len(out.route))
        results = run_batch(batch, ctx)
        for name, result in zip(batch, results, strict=True):
            _record(name, result, ctx, out)
        for name, result in zip(batch, results, strict=True):
            if result.status == "ok":
                continue
            nxt = next_step(result, out.route, ctx.intent)
            if not nxt or nxt in queue:  # já agendado por outro agente do mesmo lote
                continue
            ctx.handoff_reason = f"{name}_{result.reason or result.status}"
            queue.insert(0, nxt)
            out.replans.append(f"{name}:{result.reason or result.status}->{nxt}")
            METRICS.inc("replans_total", agent=name, reason=result.reason or result.status, engine="table")
    return out


def run_llm(plan: list[str], ctx: Context, llm) -> Outcome:
    out = Outcome(engine="llm")
    user = (f"Mensagem do usuário: {ctx.text}\nIntent: {ctx.intent}\nPlano sugerido pelo router: {plan}\n"
            f"Memória do usuário: {ctx.user_summary or '(nenhuma)'}")
    messages: list = [{"role": "user", "content": user}]
    last: str | None = None
    # Teto de voltas do modelo separado do teto de agentes: transição recusada não faz a rota crescer,
    # e um modelo insistente prenderia o worker.
    for _ in range(MAX_STEPS + 2):
        if len(out.route) >= MAX_STEPS:
            break
        resp = llm.create(domain.prompt("supervisor.md").format(language=ctx.language), messages, TOOLS,
                          max_tokens=8192)
        if resp.stop_reason != "tool_use":
            out.answer = "".join(b.text for b in resp.content if b.type == "text").strip() or None
            break
        messages.append({"role": "assistant", "content": resp.content})  # conteúdo inteiro (append-only)
        blocks = [b for b in resp.content if b.type == "tool_use"]
        verdicts: dict[str, dict] = {}
        approved: list[tuple] = []  # (bloco, agente)
        for block in blocks:
            name = _TOOL_TO_AGENT.get(block.name)
            if name is None or name not in _d().ALLOWED[last] or len(out.route) + len(approved) >= MAX_STEPS \
                    or (name == "escalation" and ctx.intent in _d().NO_ESCALATION) \
                    or (not supervisor_replan() and name not in plan):  # ablação: só o plano do router
                verdicts[block.id] = {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                      "content": f"transição não permitida: {last}->{block.name}"}
                continue
            approved.append((block, name))
        # escalation (terminal) roda depois dos outros do mesmo turno, sozinha
        batches = [[a for a in approved if a[1] not in SEQUENTIAL], [a for a in approved if a[1] in SEQUENTIAL]]
        remaining = [p for p in plan if p not in out.route]
        for batch in (b for b in batches if b):
            for block, name in batch:
                if name == "escalation":
                    # Motivo livre do modelo vai só para o ticket; métrica usa rótulo fixo (cardinalidade limitada).
                    ctx.handoff_reason = ctx.handoff_reason or (
                        "user_request" if name in remaining else "supervisor_decision")
                    ctx.facts["escalation_note"] = str(block.input.get("reason", ""))[:200]
                elif name == "faq" and block.input.get("query"):
                    ctx.facts.setdefault("query_hints", []).append(str(block.input["query"])[:300])
                # sub-pergunta por agente: em pergunta composta, cada um responde só a sua parte
                ask = block.input.get("query") or block.input.get("request")
                if ask:
                    ctx.facts.setdefault("ask", {})[name] = str(ask)[:300]
                if name not in remaining:
                    out.replans.append(f"{last or 'start'}->{name} (plano: {remaining or '[]'})")
                    METRICS.inc("replans_total", agent=last or "start", reason="llm_decision", engine="llm")
            results = run_batch([name for _, name in batch], ctx)
            for (block, name), result in zip(batch, results, strict=True):
                _record(name, result, ctx, out)
                verdicts[block.id] = {"type": "tool_result", "tool_use_id": block.id,
                                      "content": result.for_supervisor()}
                last = name
                if result.status != "ok" and name != "escalation":
                    ctx.handoff_reason = f"{name}_{result.reason or result.status}"
        # todos os tool_result numa mensagem só, na ordem dos blocos
        messages.append({"role": "user", "content": [verdicts[b.id] for b in blocks]})
    return out


def run(plan: list[str], ctx: Context, confidence: float = 1.0) -> Outcome:
    llm = get_llm("supervisor")
    if llm.enabled and plan:
        try:
            return run_llm(plan, ctx, llm)
        except LLMError:
            METRICS.inc("supervisor_fallback_total")
            ctx.facts.clear()  # recomeça limpo no motor determinístico
            ctx.handoff_reason = None
    return run_table(plan, ctx, confidence)
