"""Servidor MCP em /mcp: o mesmo atendimento, a busca e as ferramentas do domínio para qualquer cliente MCP.

A vaga (AI Hardcore Engineer) cobra MCP; um agente de outra equipe ou o Claude Desktop consome o sistema
sem integração sob medida. Transporte streamable HTTP, sem estado (cada chamada se basta), montado no
FastAPI. SDK `mcp` 2.x (MCPServer; o antigo FastMCP).

Segurança: o MCP não traz autenticação própria. `require_bearer` recusa com 401 qualquer requisição sem
JWT válido ANTES de chegar ao protocolo (nem lista ferramentas), e cada ferramenta relê o token da própria
requisição: a identidade vem do servidor, nunca de argumento. Escrita segue a mecânica de actions.py
(confirmação do dono; risco alto, aprovação do operador).
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from .actions import run_tool
from .agents import covered_hits
from .auth import verify_token
from .observability import METRICS
from .rag import get_retriever
from .schemas import ChatRequest
from .tools import registry

server = MCPServer(name="support-agents", instructions=(
    "Atendimento multiagente. Use `ask` para uma pergunta do cliente (o sistema roteia, busca, consulta "
    "dados e escala para humano quando preciso); `search_knowledge` para trechos da base com fonte; "
    "`crm_tool` para uma ferramenta do domínio (escrita só cria ação pendente de confirmação)."))


def _user(ctx: Context) -> str:
    req = ctx.request_context.request
    token = (req.headers.get("authorization", "") if req is not None else "").removeprefix("Bearer ").strip()
    user_id = verify_token(token)
    if not user_id:  # require_bearer já barrou; isto é defesa em profundidade
        raise PermissionError("token ausente, expirado ou inválido")
    return user_id


@server.tool(description="Envia uma mensagem ao atendimento como o cliente autenticado e devolve a resposta "
                         "com intenção, fontes, ação pendente e handoff.")
def ask(message: str, ctx: Context, conversation_id: str | None = None) -> dict[str, Any]:
    from .pipeline import handle  # import tardio: pipeline importa muita coisa

    METRICS.inc("mcp_calls_total", tool="ask")
    resp = handle(ChatRequest(message=message, conversation_id=conversation_id), _user(ctx))
    return resp.model_dump(include={"answer", "intent", "conversation_id", "sources", "pending_action", "handoff",
                                    "unsupported_numbers", "trace_id"})


@server.tool(description="Busca trechos da base de conhecimento (com o mesmo portão de cobertura do agente).")
def search_knowledge(query: str, ctx: Context, k: int = 3) -> list[dict[str, Any]]:
    _user(ctx)
    METRICS.inc("mcp_calls_total", tool="search_knowledge")
    hits = covered_hits(get_retriever().search(query, k=max(1, min(k, 5))))
    return [{"id": h.doc.id, "title": h.doc.title, "url": h.doc.url, "text": h.doc.text[:600]} for h in hits]


@server.tool(description="Executa uma ferramenta do domínio como o cliente autenticado. Escrita devolve "
                         "needs_confirmation + action_id (confirmar em POST /actions/{id}/confirm).")
def crm_tool(name: str, ctx: Context, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    user_id = _user(ctx)
    METRICS.inc("mcp_calls_total", tool="crm_tool")
    if name not in registry():
        return {"ok": False, "error": f"unknown_tool:{name}", "available": sorted(registry())}
    result, _ = run_tool(name, arguments or {}, user_id)  # identidade do token, nunca do argumento
    return result


def require_bearer(app):
    """ASGI: 401 sem JWT válido, antes do protocolo MCP (nem o tools/list é anônimo)."""

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            token = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not verify_token(token):
                METRICS.inc("mcp_auth_failures_total")
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"detail":"token ausente ou invalido"}'})
                return
        await app(scope, receive, send)

    return guarded


def http_app():
    """App ASGI para montar em /mcp (o lifespan do FastAPI precisa entrar em server.session_manager.run())."""
    return require_bearer(server.streamable_http_app(streamable_http_path="/", stateless_http=True,
                                                     json_response=True))
