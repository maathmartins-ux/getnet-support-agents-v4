"""API HTTP.

Cliente: POST /chat · GET /conversations/{id} · POST /actions/{id}/confirm · POST /feedback · POST /auth/revoke
  AUTH_MODE=required → identidade só do Bearer JWT assinado (corpo ignorado).
  AUTH_MODE=demo     → Bearer válido vence; sem ele, user_id do corpo (identity_verified=false,
                       rate limit por IP). Ver docs/adr/0001-auth-mode-demo.md.
Operador (X-Operator-Token = chave pessoal ou token compartilhado "admin"):
  GET /operator (console) · GET /handoffs · POST /handoffs/{id}/reply (retoma a conversa na hora)
  POST /actions/{id}/approve | /reject (ação de risco alto) · POST /handoffs/sla-check · GET /analytics
MCP (JWT obrigatório): /mcp — ferramentas ask, search_knowledge e crm_tool para qualquer cliente MCP.
Telegram (opcional): POST /telegram/webhook — resposta do operador pelo chat volta para a conversa.
Infra: GET /health (com contagem de dados) · GET /metrics
"""
from __future__ import annotations

import asyncio
import hmac
import os
import socket
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import __version__, mcp_server, notify
from .actions import confirm_action, decide_action
from .analytics import summarize
from .auth import LIMITER, operator_auth_mode, operator_name, revoke_token, verify_token
from .config import auth_mode, auth_secret, data_dir, llm_provider, operator_token, retriever_name
from .llm import warmup
from .observability import METRICS, configure_otel, log, otel_enabled
from .pipeline import handle
from .rag import get_retriever
from .resume import resume_after_human
from .schemas import ChatRequest, ChatResponse
from .store import get_store

OPERATOR_HTML = (Path(__file__).parent / "static" / "operator.html").read_text(encoding="utf-8")
MCP_APP = mcp_server.http_app()  # uma vez: cria o session manager que o lifespan abre


def insecure_defaults() -> list[str]:
    """Segredo padrão ou curto torna o token forjável. Visível no /health e no log."""
    return [name for name, value in (("AUTH_SECRET", auth_secret()), ("OPERATOR_TOKEN", operator_token()))
            if value.startswith("dev-") or len(value) < 16]


def sla_check(now: float | None = None) -> list[dict]:
    """Re-escalona uma vez os casos com SLA vencido e avisa de novo o canal humano."""
    escalated = get_store().escalate_overdue(now)
    for ticket in escalated:
        METRICS.inc("sla_escalations_total", kind=ticket["kind"])
        notify.notify_new_ticket({**ticket, "reason": f"SLA vencido ({ticket['reason']})"})
    return escalated


async def _sla_loop(seconds: int) -> None:
    while True:
        await asyncio.sleep(seconds)
        with suppress(Exception):  # o laço de SLA nunca derruba a API
            await asyncio.to_thread(sla_check)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bad = insecure_defaults()
    if bad and os.getenv("APP_ENV") == "production":
        raise RuntimeError(f"segredos padrão em produção: {bad}")  # falha alto em vez de subir forjável
    if bad:
        log.warning("insecure_defaults", extra={"extra_fields": {"vars": bad}})
    configure_otel()
    warmup()  # SDK e clientes prontos antes do 1º request (o import custa segundos; ver llm.warmup)
    get_retriever()  # índice (e embeddings, no híbrido) prontos antes do 1º request e do healthcheck
    seconds = int(os.getenv("SLA_CHECK_SECONDS", "60"))  # 0 desliga (ex.: réplica sem responsabilidade de SLA)
    task = asyncio.create_task(_sla_loop(seconds)) if seconds > 0 else None
    # o app MCP montado não tem lifespan próprio sob o FastAPI: sem isto, "Task group is not initialized"
    async with mcp_server.server.session_manager.run():
        yield
    if task:
        task.cancel()


app = FastAPI(title="app", version=__version__, lifespan=lifespan)
app.mount("/mcp", MCP_APP)  # streamable HTTP; 401 sem JWT (mcp_server.require_bearer)
INSTANCE = os.getenv("INSTANCE_NAME") or socket.gethostname()


@app.middleware("http")
async def instance_header(request: Request, call_next):
    # Qual réplica atendeu: o teste de réplicas (scripts/scale_smoke.py) prova que a conversa continua entre elas.
    response = await call_next(request)
    response.headers["X-Instance"] = INSTANCE
    return response


class OperatorReply(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


class Decision(BaseModel):
    answer: str = Field(default="", max_length=1000)


class Feedback(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    rating: int = Field(ge=-1, le=1)  # 1 = 👍, -1 = 👎 (0 é recusado abaixo: sem opinião não é feedback)
    comment: str | None = Field(default=None, max_length=500)


def _bearer(authorization: str | None) -> str | None:
    return authorization.removeprefix("Bearer ").strip() if authorization else None


def _identity(authorization: str | None, fallback: str | None = None) -> tuple[str, bool]:
    """(user_id, identity_verified). Token presente e inválido é sempre 401, também no modo demo."""
    token = _bearer(authorization)
    if token:
        user_id = verify_token(token)
        if user_id:
            return user_id, True
    elif auth_mode() == "demo" and fallback:
        return fallback, False
    METRICS.inc("auth_failures_total")
    raise HTTPException(401, "token ausente, expirado ou inválido", headers={"WWW-Authenticate": "Bearer"})


def _require_operator(token: str | None) -> str:
    name = operator_name(token)
    if not name:
        METRICS.inc("operator_auth_failures_total")
        raise HTTPException(403, "operador não autorizado")
    return name


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request, authorization: str | None = Header(default=None)) -> ChatResponse:
    user_id, verified = _identity(authorization, req.user_id)
    # Sem identidade verificada, trocar o user_id do corpo não pode zerar o limite: a chave vira o IP.
    key = user_id if verified else f"ip:{request.client.host if request.client else 'unknown'}"
    if not LIMITER.allow(key):
        METRICS.inc("rate_limited_total")
        raise HTTPException(429, "muitas requisições; tente em 1 minuto", headers={"Retry-After": "60"})
    try:
        return handle(req, user_id, verified)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/auth/revoke")
def revoke(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token or not revoke_token(token):
        raise HTTPException(401, "token ausente, expirado ou inválido")
    return {"revoked": True}


@app.get("/conversations/{conversation_id}")
def conversation(conversation_id: str, user_id: str | None = None,
                 authorization: str | None = Header(default=None)) -> dict:
    # Demo: ?user_id= faz o papel do corpo do /chat; quem não é dono recebe 404 nos dois modos.
    store = get_store()
    owner = store.conversation_owner(conversation_id)
    user_id, _ = _identity(authorization, user_id)
    if owner != user_id:
        raise HTTPException(404, "conversa não encontrada")
    open_ticket = store.ticket_for(conversation_id, "open")
    return {"conversation_id": conversation_id, "messages": store.history(conversation_id, limit=100),
            "open_ticket": open_ticket["id"] if open_ticket else None}


@app.post("/actions/{action_id}/confirm")
def confirm(action_id: str, user_id: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    user_id, _ = _identity(authorization, user_id)
    result = confirm_action(action_id, user_id)
    if not result.get("ok") and result.get("error") == "not_found":
        raise HTTPException(404, "ação não encontrada")
    if result.get("error") == "expired":
        raise HTTPException(410, "ação expirada; peça de novo")
    return result


@app.post("/actions/{action_id}/approve")
def approve(action_id: str, x_operator_token: str | None = Header(default=None)) -> dict:
    out = decide_action(action_id, _require_operator(x_operator_token), approve=True)
    if out is None:
        raise HTTPException(409, "ação inexistente ou já decidida")
    return out


@app.post("/actions/{action_id}/reject")
def reject(action_id: str, body: Decision | None = None, x_operator_token: str | None = Header(default=None)) -> dict:
    out = decide_action(action_id, _require_operator(x_operator_token), approve=False,
                        note=(body.answer if body else ""))
    if out is None:
        raise HTTPException(409, "ação inexistente ou já decidida")
    return out


@app.post("/feedback")
def feedback(body: Feedback, authorization: str | None = Header(default=None)) -> dict:
    user_id, _ = _identity(authorization)
    if body.rating == 0:
        raise HTTPException(422, "rating deve ser 1 ou -1")
    store = get_store()
    if store.request_owner(body.trace_id) != user_id:  # só o dono avalia a própria resposta
        raise HTTPException(404, "atendimento não encontrado")
    store.add_feedback(body.trace_id, user_id, body.rating, body.comment)
    METRICS.inc("feedback_total", rating=str(body.rating))
    return {"ok": True}


@app.get("/analytics")
def analytics(x_operator_token: str | None = Header(default=None)) -> dict:
    _require_operator(x_operator_token)
    return summarize(*get_store().analytics_rows())


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")  # quem abre o endereço cai na documentação interativa, não num 404


@app.get("/operator", response_class=HTMLResponse)
def operator_console() -> str:
    # Página estática; os dados vêm de /handoffs com o token digitado pelo operador (nunca embutido no HTML).
    return OPERATOR_HTML


@app.get("/handoffs")
def handoffs(status: str = "open", x_operator_token: str | None = Header(default=None)) -> list[dict]:
    _require_operator(x_operator_token)
    store = get_store()
    return [{**t, "messages": store.history(t["conversation_id"], limit=10)} for t in store.list_tickets(status)]


@app.post("/handoffs/sla-check")
def handoffs_sla_check(now: float | None = None, x_operator_token: str | None = Header(default=None)) -> dict:
    _require_operator(x_operator_token)
    return {"escalated": sla_check(now)}


def _answer(ticket_id: str, answer: str, operator: str, channel: str) -> dict | None:
    ticket = get_store().answer_ticket(ticket_id, answer, operator)
    if not ticket:
        return None
    METRICS.inc("handoff_replies_total", channel=channel)
    # Retomada: o assistente escreve a próxima mensagem ao cliente agora, a partir da resposta humana.
    return {"ticket": ticket, "resumed": resume_after_human(ticket, operator)}


@app.post("/handoffs/{ticket_id}/reply")
def reply(ticket_id: str, body: OperatorReply, x_operator_token: str | None = Header(default=None)) -> dict:
    out = _answer(ticket_id, body.answer, _require_operator(x_operator_token), "console")
    if not out:
        raise HTTPException(404, "ticket não encontrado ou já respondido")
    return out


@app.post("/telegram/webhook")
def telegram_webhook(update: dict,
                     x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict:
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret or not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            x_telegram_bot_api_secret_token.encode(), secret.encode()):
        raise HTTPException(403, "webhook não autorizado")
    parsed = notify.parse_reply(update)
    if not parsed:
        return {"ok": True, "handled": False}  # 200 sempre: o Telegram reenvia update que recebeu erro
    who = ((update.get("message") or {}).get("from") or {}).get("username") or "telegram"
    out = _answer(parsed[0], parsed[1], f"tg:{who}", "telegram")
    return {"ok": True, "handled": bool(out), "ticket_id": parsed[0]}


@app.get("/health")
def health() -> dict:
    # data_files > 0 é o que o smoke do container confere: prova que o caminho dos dados
    # resolve dentro da imagem, não só na máquina de quem desenvolveu.
    files = [p for p in data_dir().rglob("*") if p.is_file() and p.name != ".gitkeep"]
    return {"status": "ok", "version": __version__, "mode": llm_provider(), "auth_mode": auth_mode(),
            "operator_auth": operator_auth_mode(), "retriever": get_retriever().name,  # efetivo, não só o pedido
            "retriever_requested": retriever_name(), "otel": otel_enabled(),
            "telegram": bool(notify.configured()), "insecure_defaults": insecure_defaults(),
            "data_files": len(files)}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    return METRICS.render()
