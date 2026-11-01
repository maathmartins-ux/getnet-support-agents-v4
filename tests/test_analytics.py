"""Sistema de trabalho: cada atendimento vira dado; 👍/👎 do cliente; painel do operador; memória rolante."""
from fastapi.testclient import TestClient

from app.analytics import summarize
from app.api import app
from app.auth import issue_token

client = TestClient(app)
OP = {"X-Operator-Token": "test-operator"}


def auth(user="u1"):
    return {"Authorization": f"Bearer {issue_token(user)}"}


def chat(message, conv=None, user="u1"):
    return client.post("/chat", json={"message": message, "conversation_id": conv}, headers=auth(user)).json()


def test_summary_math():
    reqs = [{"intent": "faq", "handoff": 0, "blocked": 0, "ungrounded": 0, "cost_usd": 0.01, "ms": 100.0},
            {"intent": "faq", "handoff": 1, "blocked": 0, "ungrounded": 0, "cost_usd": 0.02, "ms": 300.0},
            {"intent": "records", "handoff": 0, "blocked": 0, "ungrounded": 1, "cost_usd": 0.01, "ms": 200.0},
            {"intent": "blocked", "handoff": 0, "blocked": 1, "ungrounded": 0, "cost_usd": 0.0, "ms": 5.0}]
    s = summarize(reqs, [{"rating": 1}, {"rating": -1}, {"rating": 1}])
    assert s["requests"] == 4 and s["resolution_rate"] == 0.25 and s["escalation_rate"] == 0.25
    assert s["cost_usd_per_resolved"] == 0.04 and s["feedback"]["approval_rate"] == 0.667
    assert s["by_intent"]["faq"] == {"requests": 2, "resolution_rate": 0.5}
    assert s["worst_intents"][0] in ("records", "blocked")


def test_every_request_is_logged_and_feedback_is_owned():
    first = chat("Qual o prazo de entrega?")
    chat("Quero falar com um atendente")
    tid = first["trace_id"]
    assert client.post("/feedback", json={"trace_id": tid, "rating": 1}, headers=auth("u2")).status_code == 404
    assert client.post("/feedback", json={"trace_id": tid, "rating": 1}, headers=auth()).status_code == 200
    assert client.post("/feedback", json={"trace_id": tid, "rating": 5}, headers=auth()).status_code == 422
    assert client.get("/analytics").status_code == 403
    a = client.get("/analytics", headers=OP).json()
    assert a["requests"] == 2 and a["escalation_rate"] == 0.5 and a["feedback"]["up"] == 1
    assert a["by_intent"]["human_request"]["resolution_rate"] == 0.0


def test_rolling_digest_keeps_early_facts_beyond_the_window():
    conv = chat("Onde está meu pedido 1002?")["conversation_id"]
    for _ in range(11):  # 22 mensagens: o pedido 1002 sai da janela recente de 20
        chat("Qual o prazo de entrega?", conv)
    follow = chat("E pode cancelar ele?", conv)
    assert follow["intent"] == "records" and follow["tool_calls"][0]["args"] == {"record_id": "1002"}
