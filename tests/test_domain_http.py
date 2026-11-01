"""Contrato HTTP do domínio (frente Q): vale independente do router e das tools das outras frentes.

Não afirma a intenção exata de cada cenário (isso é o eval); afirma o envelope: 200, campos do JSON, identidade
honesta (demo → identity_verified false), 401 para token ruim, token vence o corpo, PII não volta, 422 e 429.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.api import app
from app.auth import LIMITER, issue_token
from app.config import ROOT

client = TestClient(app)

# Os 10 cenários do enunciado (SPEC, AC-01..AC-10), lidos do arquivo congelado: nenhuma frase copiada aqui.
_CASES = (ROOT / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
OFFICIAL = [c for c in map(json.loads, _CASES) if c["split"] == "official"]


def _forge(claims: dict, alg: str = "HS256", sign: bool = True) -> str:
    head = auth._b64e(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = auth._b64e(json.dumps(claims).encode())
    sig = auth._mac(f"{head}.{body}") if sign else ""
    return f"{head}.{body}.{sig}"


def _claims(**over) -> dict:
    now = int(time.time())
    base = {"sub": "cliente1988", "iat": now, "exp": now + 600, "jti": "q-test",
            "iss": auth.auth_issuer(), "aud": auth.auth_audience()}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def test_official_scenarios_are_ten():
    assert len(OFFICIAL) == 10


@pytest.mark.parametrize("case", OFFICIAL, ids=lambda c: c["id"])
def test_official_scenario_over_http_in_demo_mode(domain_data, monkeypatch, case):
    monkeypatch.setenv("AUTH_MODE", "demo")
    r = client.post("/chat", json={"message": case["message"], "user_id": case["user_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    for field in ("answer", "intent", "route", "trace_id", "usage"):
        assert field in body, field
    assert body["answer"].strip() and body["trace_id"]
    assert isinstance(body["route"], list) and isinstance(body["usage"], dict)
    assert body["identity_verified"] is False and body["user_id"] == "cliente1988"


def test_required_mode_without_token_is_401(domain_data):
    r = client.post("/chat", json={"message": "Quanto vendi ontem?", "user_id": "cliente1988"})
    assert r.status_code == 401 and r.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.parametrize("token_kind", ["no_exp", "expired", "alg_none", "bad_signature", "garbage"])
def test_bad_tokens_are_401(domain_data, token_kind):
    token = {
        "no_exp": lambda: _forge(_claims(exp=None)),
        "expired": lambda: _forge(_claims(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)),
        "alg_none": lambda: _forge(_claims(), alg="none", sign=False),
        "bad_signature": lambda: issue_token("cliente1988")[:-4] + "AAAA",
        "garbage": lambda: "cliente1988",
    }[token_kind]()
    r = client.post("/chat", json={"message": "Quanto vendi ontem?"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_bad_token_is_401_even_in_demo_mode(domain_data, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "demo")
    r = client.post("/chat", json={"message": "Oi", "user_id": "cliente1988"},
                    headers={"Authorization": "Bearer forjado.forjado.forjado"})
    assert r.status_code == 401


def test_revoked_token_is_401(domain_data):
    token = issue_token("cliente1988")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/chat", json={"message": "Oi"}, headers=headers).status_code == 200
    assert client.post("/auth/revoke", headers=headers).json() == {"revoked": True}
    assert client.post("/chat", json={"message": "Oi"}, headers=headers).status_code == 401


@pytest.mark.parametrize("mode", ["required", "demo"])
def test_valid_token_wins_over_body_user_id(domain_data, monkeypatch, mode):
    monkeypatch.setenv("AUTH_MODE", mode)
    headers = {"Authorization": f"Bearer {issue_token('cliente1988')}"}
    r = client.post("/chat", json={"message": "Quanto vendi ontem?", "user_id": "cliente2024"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["user_id"] == "cliente1988" and r.json()["identity_verified"] is True


def test_pii_from_the_request_does_not_come_back(domain_data):
    headers = {"Authorization": f"Bearer {issue_token('cliente1988')}"}
    pii = ["529.982.247-25", "4111 1111 1111 1111", "maria.souza@example.com", "(51) 99876-5432"]
    msg = (f"Meu CPF é {pii[0]}, cartão {pii[1]}, e-mail {pii[2]} e telefone {pii[3]}. "
           "Quando recebo as vendas de ontem?")
    r = client.post("/chat", json={"message": msg}, headers=headers)
    assert r.status_code == 200
    raw = r.text
    assert not any(p in raw for p in pii)
    conv = r.json()["conversation_id"]
    stored = client.get(f"/conversations/{conv}", headers=headers).text
    assert not any(p in stored for p in pii)


def test_message_over_4000_chars_is_422(domain_data, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "demo")
    r = client.post("/chat", json={"message": "a" * 4001, "user_id": "cliente1988"})
    assert r.status_code == 422


def test_rate_limit_returns_429_with_retry_after(domain_data, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    LIMITER.reset()
    headers = {"Authorization": f"Bearer {issue_token('cliente1988')}"}
    codes = [client.post("/chat", json={"message": "Oi"}, headers=headers) for _ in range(3)]
    assert [c.status_code for c in codes[:2]] == [200, 200]
    assert codes[2].status_code == 429 and codes[2].headers.get("Retry-After")


def test_demo_rate_limit_is_by_ip_so_switching_user_id_does_not_reset_it(domain_data, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "demo")
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    LIMITER.reset()
    codes = [client.post("/chat", json={"message": "Oi", "user_id": f"forjado{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]
