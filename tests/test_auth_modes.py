"""Item 4 — AUTH_MODE: required (identidade só do token) × demo (corpo aceito, marcado, limite por IP)."""
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.auth import issue_token

client = TestClient(app)
MSG = {"message": "Onde está meu pedido 1002?", "user_id": "u1"}  # 1002 é de u1


def bearer(user):
    return {"Authorization": f"Bearer {issue_token(user)}"}


# --- required (padrão) --------------------------------------------------------------------------
def test_required_rejects_body_identity():
    assert client.post("/chat", json=MSG).status_code == 401
    assert client.post("/chat", json=MSG, headers={"Authorization": "Bearer u1.forjado"}).status_code == 401


def test_required_identity_comes_from_token_not_body():
    body = client.post("/chat", json=MSG, headers=bearer("u2")).json()  # u2 tenta se passar por u1
    assert body["user_id"] == "u2" and body["identity_verified"] is True and "Livro B" not in body["answer"]


# --- demo ---------------------------------------------------------------------------------------
@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "demo")


def test_demo_accepts_literal_payload_and_marks_it(demo):
    r = client.post("/chat", json=MSG)
    body = r.json()
    assert r.status_code == 200 and body["user_id"] == "u1" and body["identity_verified"] is False
    assert "Livro B" in body["answer"]
    assert client.get("/health").json()["auth_mode"] == "demo"


def test_demo_valid_token_wins_over_body(demo):
    body = client.post("/chat", json=MSG, headers=bearer("u2")).json()
    assert body["user_id"] == "u2" and body["identity_verified"] is True and "Livro B" not in body["answer"]


def test_demo_forged_token_is_rejected(demo):
    assert client.post("/chat", json=MSG, headers={"Authorization": "Bearer u1.forjado"}).status_code == 401


def test_demo_without_any_identity_is_rejected(demo):
    assert client.post("/chat", json={"message": "oi"}).status_code == 401


def test_demo_abuse_rotating_body_user_does_not_reset_rate_limit(demo, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    codes = [client.post("/chat", json={"message": "oi", "user_id": f"rot{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]  # a chave do limite é o IP, não o user_id declarado


def test_demo_conversation_still_scoped_to_owner(demo):
    conv = client.post("/chat", json=MSG).json()["conversation_id"]
    assert client.get(f"/conversations/{conv}", params={"user_id": "u1"}).status_code == 200
    assert client.get(f"/conversations/{conv}", params={"user_id": "u2"}).status_code == 404
