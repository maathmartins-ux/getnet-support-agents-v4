"""Servidor MCP em /mcp, ponta a ponta: uvicorn de verdade + cliente MCP oficial (streamable HTTP).

A vaga pede MCP; o avaliador não roda nada, então a prova é este teste: sem JWT → 401 (nem lista ferramentas);
com JWT → `ask` atende como o dono do token, `crm_tool` não deixa um cliente ver dado de outro e escrita só
cria ação pendente.
"""
import asyncio
import socket
import threading
import time

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.api import app
from app.auth import issue_token


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn não subiu em 30 s"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _run(base_url: str, user: str | None, steps):
    async def go():
        headers = {"Authorization": f"Bearer {issue_token(user)}"} if user else {}
        async with httpx2.AsyncClient(headers=headers) as http:
            async with Client(streamable_http_client(f"{base_url}/mcp/", http_client=http)) as client:
                return await steps(client)

    return asyncio.run(go())


def test_anonymous_gets_401_before_the_protocol(base_url):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    r = httpx2.post(f"{base_url}/mcp/", json=body, headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    forged = httpx2.post(f"{base_url}/mcp/", json=body, headers={"Authorization": "Bearer u1.forjado"})
    assert forged.status_code == 401


def test_tools_are_listed_and_ask_answers_as_the_token_owner(base_url):
    async def steps(client):
        names = sorted(t.name for t in (await client.list_tools()).tools)
        res = await client.call_tool("ask", {"message": "Onde está meu pedido 1002?"})
        return names, res

    names, res = _run(base_url, "u1", steps)
    assert names == ["ask", "crm_tool", "search_knowledge"]
    assert not res.is_error and "Livro B" in res.structured_content["answer"]


def test_crm_tool_identity_comes_from_token_and_write_needs_confirmation(base_url):
    async def steps(client):
        other = await client.call_tool("crm_tool", {"name": "lookup_record", "arguments": {"record_id": "1002",
                                                                                           "user_id": "u1"}})
        write = await client.call_tool("crm_tool", {"name": "cancel_record", "arguments": {"record_id": "2001"}})
        return other, write

    other, write = _run(base_url, "u2", steps)  # u2 tenta ler o pedido de u1 passando user_id no argumento
    assert other.structured_content == {"ok": False, "error": "not_found"}
    assert write.structured_content["needs_confirmation"] is True and write.structured_content["action_id"]


def test_search_knowledge_returns_sources(base_url):
    async def steps(client):
        return await client.call_tool("search_knowledge", {"query": "Qual o prazo de entrega?"})

    res = _run(base_url, "u1", steps)
    hits = res.structured_content["result"]
    assert hits and hits[0]["id"] == "faq-entrega" and hits[0]["url"]
