"""Supervisão humana de produção (feedback v2, 31/10/2026: "HITL não retoma o plano", "sem SLA ou timeout de
caso", "lock da confirmação em processo e sem TTL na ação pendente").

- resposta do operador → o assistente escreve na hora a próxima mensagem ao cliente a partir dela (retomada);
- ação de risco alto: confirmação do dono E aprovação do operador (quatro olhos); executa uma vez só;
- ação pendente expira (ACTION_TTL_MIN); caso aberto com SLA vencido sobe de prioridade uma vez;
- duas respostas simultâneas ao mesmo caso: uma vence.
"""
import threading

from fakes import fake_llm, resp, text
from fastapi.testclient import TestClient

from app import actions, resume
from app.api import app
from app.auth import issue_token
from app.store import get_store

client = TestClient(app)
OP = {"X-Operator-Token": "test-operator"}


def auth(user="u1"):
    return {"Authorization": f"Bearer {issue_token(user)}"}


def chat(message, conv=None, user="u1"):
    return client.post("/chat", json={"message": message, "conversation_id": conv}, headers=auth(user)).json()


# --- retomada --------------------------------------------------------------------------------------
def test_operator_reply_resumes_the_conversation_immediately():
    r0 = chat("Quero falar com um atendente")
    ticket, conv = r0["handoff"]["ticket_id"], r0["conversation_id"]
    r = client.post(f"/handoffs/{ticket}/reply", json={"answer": "Seu limite de antecipação é R$ 5.000,00."},
                    headers=OP).json()
    assert r["ticket"]["status"] == "answered" and r["ticket"]["answered_by"] == "admin"
    assert "5.000,00" in r["resumed"]["answer"]
    msgs = client.get(f"/conversations/{conv}", headers=auth()).json()["messages"]
    assert [m["role"] for m in msgs][-2:] == ["human_agent", "assistant"]  # sem esperar o próximo turno
    assert msgs[-1]["intent"] == "resumed"


def test_resume_with_llm_treats_human_answer_as_authority(monkeypatch):
    llm, msgs = fake_llm([resp("end_turn", text("O atendente confirmou: limite de R$ 5.000,00."))], effort="medium")
    monkeypatch.setattr(resume, "get_llm", lambda role="agent": llm)
    r0 = chat("Quero falar com um atendente")
    out = client.post(f"/handoffs/{r0['handoff']['ticket_id']}/reply",
                      json={"answer": "Limite de R$ 5.000,00."}, headers=OP).json()
    sent = msgs.sent[0]
    assert "Limite de R$ 5.000,00." in sent["messages"][-1]["content"] and "autoridade" in sent["system"]
    assert out["resumed"]["answer"].startswith("O atendente confirmou") and out["resumed"]["unsupported_numbers"] == []


def test_two_simultaneous_replies_only_one_wins():
    ticket = chat("Quero falar com um atendente")["handoff"]["ticket_id"]
    codes = []

    def answer(i):
        codes.append(client.post(f"/handoffs/{ticket}/reply", json={"answer": f"resposta {i}"}, headers=OP).status_code)

    threads = [threading.Thread(target=answer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 404, 404, 404]


# --- aprovação de quatro olhos ---------------------------------------------------------------------------
def test_high_risk_action_needs_owner_then_operator_and_runs_once():
    r = chat("Quero o estorno do pedido 1001")
    aid = r["pending_action"]
    assert aid and "Nada foi feito" in r["answer"]
    owner = client.post(f"/actions/{aid}/confirm", headers=auth()).json()
    assert owner["awaiting_operator"] is True and owner["ticket_id"]
    assert get_store().get_action(aid)["status"] == "awaiting_operator"  # dono confirmou, ainda não executou
    queue = client.get("/handoffs", headers=OP).json()
    assert any(t["kind"] == "approval" and t["action_id"] == aid for t in queue)

    assert client.post(f"/actions/{aid}/approve", headers={"X-Operator-Token": "errado"}).status_code == 403
    done = client.post(f"/actions/{aid}/approve", headers=OP).json()
    assert done["result"]["ok"] is True and done["action"]["approved_by"] == "admin"
    assert client.post(f"/actions/{aid}/approve", headers=OP).status_code == 409  # executa uma vez só
    conv = r["conversation_id"]
    last = client.get(f"/conversations/{conv}", headers=auth()).json()["messages"][-1]
    assert last["role"] == "assistant" and "aprovad" in last["content"].lower()


def test_rejected_high_risk_action_runs_nothing():
    aid = chat("Quero o estorno do pedido 1001")["pending_action"]
    client.post(f"/actions/{aid}/confirm", headers=auth())
    out = client.post(f"/actions/{aid}/reject", json={"answer": "Fora da política."}, headers=OP).json()
    assert out["action"]["status"] == "rejected" and out["result"] is None
    assert client.post(f"/actions/{aid}/approve", headers=OP).status_code == 409


def test_normal_write_action_still_runs_on_owner_confirmation():
    aid = chat("Quero cancelar o pedido 1001")["pending_action"]
    assert client.post(f"/actions/{aid}/confirm", headers=auth()).json()["ok"] is True


# --- TTL e SLA ------------------------------------------------------------------------------------------
def test_pending_action_expires(monkeypatch):
    aid = chat("Quero cancelar o pedido 1001")["pending_action"]
    real = actions.time.time
    monkeypatch.setattr(actions.time, "time", lambda: real() + 16 * 60)  # TTL padrão: 15 min
    r = client.post(f"/actions/{aid}/confirm", headers=auth())
    assert r.status_code == 410 and get_store().get_action(aid)["status"] == "expired"


def test_overdue_case_is_escalated_once():
    ticket = chat("Quero falar com um atendente")["handoff"]["ticket_id"]
    store = get_store()
    assert store.escalate_overdue() == []  # ainda no prazo
    due = store.get_ticket(ticket)["due_at"]
    first = client.post("/handoffs/sla-check", params={"now": due + 1}, headers=OP).json()
    assert [t["id"] for t in first["escalated"]] == [ticket]
    assert store.get_ticket(ticket)["priority"] == "high" and store.get_ticket(ticket)["escalation_level"] == 1
    assert client.post("/handoffs/sla-check", params={"now": due + 2}, headers=OP).json()["escalated"] == []
