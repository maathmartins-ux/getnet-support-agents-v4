"""Item 6 — Telegram opcional: transporte falso (httpx.MockTransport), nunca quebra o fluxo, volta pelo webhook."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import notify
from app.api import app
from app.auth import issue_token

client = TestClient(app)
TICKET = {"id": "T-0a1b2c3d", "reason": "user_request", "user_id": "u1", "summary": "[human_request] atendente"}


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(notify, "_spawn", lambda fn, *args: fn(*args))  # síncrono no teste
    sent = []

    def use(handler):
        def wrapped(request):
            sent.append(request)
            return handler(request)
        monkeypatch.setattr(notify, "_transport", httpx.MockTransport(wrapped))
    return sent, use


def test_disabled_without_env():
    assert notify.configured() is None and notify.notify_new_ticket(TICKET) is False


def test_sends_ticket_to_bot_api(telegram):
    sent, use = telegram
    use(lambda r: httpx.Response(200, json={"ok": True}))
    assert notify.notify_new_ticket(TICKET) is True
    req = sent[0]
    assert req.url.path == "/bot123:abc/sendMessage"
    payload = json.loads(req.content)
    assert payload["chat_id"] == "42" and "/responder T-0a1b2c3d" in payload["text"]


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(401, json={"ok": False}),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("sem rede", request=r)),
])
def test_send_failure_is_swallowed(telegram, handler):
    _, use = telegram
    use(handler)
    assert notify.send("123:abc", "42", "oi") is False  # devolve False, não lança


def test_chat_flow_survives_broken_telegram(telegram):
    _, use = telegram
    use(lambda r: (_ for _ in ()).throw(httpx.ConnectError("sem rede", request=r)))
    r = client.post("/chat", json={"message": "Quero falar com um atendente"},
                    headers={"Authorization": f"Bearer {issue_token('u1')}"})
    assert r.status_code == 200 and r.json()["handoff"]["ticket_id"]  # caso aberto mesmo sem aviso


def update(text, chat_id=42):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def test_reply_from_telegram_resumes_conversation(telegram, monkeypatch):
    _, use = telegram
    use(lambda r: httpx.Response(200, json={"ok": True}))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
    headers = {"Authorization": f"Bearer {issue_token('u1')}"}
    resp = client.post("/chat", json={"message": "Quero falar com um atendente"}, headers=headers).json()
    ticket, conv = resp["handoff"]["ticket_id"], resp["conversation_id"]

    hook = {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"}
    assert client.post("/telegram/webhook", json=update(f"/responder {ticket} Oi"), headers={}).status_code == 403
    assert client.post("/telegram/webhook", json=update(f"/responder {ticket} Oi", chat_id=99),
                       headers=hook).json()["handled"] is False  # outro chat não responde caso
    r = client.post("/telegram/webhook", json=update(f"/responder {ticket} Olá, aqui é a Ana."), headers=hook)
    assert r.json() == {"ok": True, "handled": True, "ticket_id": ticket}
    msgs = client.get(f"/conversations/{conv}", headers=headers).json()["messages"]
    assert msgs[-2] == {**msgs[-2], "role": "human_agent", "content": "Olá, aqui é a Ana."}
    assert msgs[-1]["role"] == "assistant" and "Olá, aqui é a Ana." in msgs[-1]["content"]  # retomada na hora
