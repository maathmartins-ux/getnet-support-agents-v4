"""Adaptador Telegram opcional (Bot API direta via httpx): avisa o operador e recebe a resposta dele.

Ativo só com TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no ambiente. Melhor esforço: falha de rede, token
errado ou API fora do ar nunca derrubam o atendimento — o caso já está persistido na fila e visível
em /operator. O texto vai sem PII (o resumo do ticket é montado da mensagem já mascarada).

Ida:   novo ticket → sendMessage no chat do operador (thread em segundo plano).
Volta: o operador responde no Telegram "/responder T-xxxx texto" → webhook POST /telegram/webhook
       (header X-Telegram-Bot-Api-Secret-Token = TELEGRAM_WEBHOOK_SECRET; só o chat configurado vale)
       → mesma rotina do console (store.answer_ticket) → a conversa do cliente retoma.
"""
from __future__ import annotations

import os
import re
import threading

import httpx

from .observability import METRICS, log

API = "https://api.telegram.org"
_REPLY = re.compile(r"^/(?:responder|reply)(?:@\w+)?\s+(T-[0-9a-f]{8})\s+(.+)$", re.IGNORECASE | re.DOTALL)
_transport: httpx.BaseTransport | None = None  # testes injetam httpx.MockTransport


def configured() -> tuple[str, str] | None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    return (token, chat_id) if token and chat_id else None


def ticket_text(ticket: dict) -> str:
    return (f"Novo caso {ticket['id']} ({ticket['reason']})\nUsuário: {ticket['user_id']}\n{ticket['summary']}\n\n"
            f"Responda aqui com: /responder {ticket['id']} <sua resposta>\nou pelo console /operator.")


def send(token: str, chat_id: str, text: str) -> bool:
    """Envia e devolve True/False. Nunca lança exceção."""
    try:
        with httpx.Client(transport=_transport, timeout=5) as client:
            client.post(f"{API}/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": text[:4000]}).raise_for_status()
        METRICS.inc("handoff_notifications_total", channel="telegram", ok="true")
        return True
    except Exception as exc:  # rede, 401, 5xx: o caso segue na fila
        METRICS.inc("handoff_notifications_total", channel="telegram", ok="false")
        # Só o tipo: a mensagem do httpx traz a URL com o token do bot.
        log.warning("telegram_send_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        return False


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()  # não segura a resposta ao cliente


def notify_new_ticket(ticket: dict) -> bool:
    """True se o envio foi disparado (canal configurado)."""
    cfg = configured()
    if not cfg:
        return False
    _spawn(send, cfg[0], cfg[1], ticket_text(ticket))
    return True


def parse_reply(update: dict) -> tuple[str, str] | None:
    """(ticket_id, resposta) se o update é um /responder vindo do chat do operador; senão None."""
    cfg = configured()
    msg = update.get("message") or {}
    if not cfg or str((msg.get("chat") or {}).get("id")) != cfg[1]:
        return None
    found = _REPLY.match((msg.get("text") or "").strip())
    return (found.group(1), found.group(2).strip()) if found else None
