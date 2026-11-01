"""Retomada depois do humano: a resposta do operador volta à conversa e o assistente segue NA HORA.

Feedback v2 (31/10/2026): "a resposta humana não retoma o plano, aparece só no próximo turno". Agora,
ao responder um caso (console, API ou Telegram), o assistente redige a próxima mensagem ao cliente a
partir da resposta humana, que é a autoridade (prompts/resume.md):
- com LLM: papel "agent", histórico curto + resposta humana;
- sem LLM, ou se ele falhar: entrega a resposta humana literal, identificada.
A mensagem passa pela máscara de PII e pela checagem numérica (evidência = resposta humana + falas do
cliente) e entra no histórico com intent "resumed". O cliente vê sem precisar escrever de novo.
"""
from __future__ import annotations

from . import domain
from .grounding import unsupported_numbers
from .guardrails import mask_output
from .llm import LLMError, get_llm
from .observability import METRICS, log, new_trace_id, span
from .router import detect_language
from .store import get_store

LABEL = {"pt": "Resposta do atendente", "en": "Agent's reply"}


def resume_after_human(ticket: dict, operator: str) -> dict:
    store = get_store()
    conv, user, answer = ticket["conversation_id"], ticket["user_id"], ticket["answer"] or ""
    history = store.history(conv, limit=12)
    said = [m["content"] for m in history if m["role"] == "user"]
    language = detect_language(said[-1] if said else answer)
    text, engine = f"{LABEL[language]}: {answer}", "template"
    llm = get_llm("agent")
    if llm.enabled:
        new_trace_id()
        msgs = [{"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]}
                for m in history if m["role"] in ("user", "assistant")][-6:]
        msgs.append({"role": "user", "content": f"<resposta_do_atendente operador=\"{operator}\">\n{answer}\n"
                                                "</resposta_do_atendente>\nRedija a próxima mensagem ao cliente."})
        if msgs[0]["role"] != "user":  # a API exige começar pelo usuário
            msgs = msgs[1:]
        try:
            with span("resume", ticket=ticket["id"]):
                reply = llm.complete(domain.prompt("resume.md").format(language=language), msgs, max_tokens=1024)
            if reply.text.strip():
                text, engine = reply.text.strip(), "llm"
        except LLMError as exc:  # a resposta humana chega mesmo se o modelo cair
            log.warning("resume_llm_failed", extra={"extra_fields": {"kind": exc.kind}})
    text = mask_output(text, public=answer)
    flagged = unsupported_numbers(text, [answer], question=" ".join(said))
    store.add_message(conv, user, "assistant", text, "resumed")
    METRICS.inc("handoff_resumed_total", engine=engine)
    return {"answer": text, "engine": engine, "unsupported_numbers": flagged}
