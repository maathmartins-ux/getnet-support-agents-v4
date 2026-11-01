"""Identidade de produção (feedback v2, 31/10/2026: "token sem expiração", "operador com token compartilhado",
"rate limit em memória").

- token = JWT HS256 padrão (sub, iat, exp, jti, iss, aud), verificável por qualquer biblioteca JWT;
- expirado, adulterado, `alg` diferente de HS256, `aud`/`iss` errados ou `jti` revogado → 401;
- operador com chave pessoal (só o hash fica na configuração) → nome dele em cada resposta (auditoria);
- limite de uso no SQLite: vale entre processos e réplicas no mesmo volume, e falha fechado.
"""
import base64
import hashlib
import json
import threading

from fastapi.testclient import TestClient

from app import auth
from app.api import app
from app.auth import issue_token, verify_token
from app.store import Store, get_store

client = TestClient(app)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


# --- token ---------------------------------------------------------------------------------------
def test_token_is_a_standard_jwt_with_expiry():
    token = issue_token("u1", ttl_min=5)
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    claims = _claims(token)
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert claims["sub"] == "u1" and claims["exp"] - claims["iat"] == 300 and claims["jti"]
    assert verify_token(token) == "u1"


def test_expired_token_is_rejected(monkeypatch):
    token = issue_token("u1", ttl_min=1)
    real = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + 61 + auth.LEEWAY_S)
    assert verify_token(token) is None


def test_tampered_alg_none_and_wrong_audience_are_rejected(monkeypatch):
    token = issue_token("u1")
    head, body, sig = token.split(".")
    forged_body = _b64({**_claims(token), "sub": "u2"})
    assert verify_token(f"{head}.{forged_body}.{sig}") is None  # trocou o sub, assinatura não bate
    assert verify_token(f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{body}.") is None
    monkeypatch.setenv("AUTH_AUDIENCE", "outra-api")
    assert verify_token(token) is None


def test_revoked_token_is_rejected_via_api():
    token = issue_token("u1")
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/chat", json={"message": "oi"}, headers=h).status_code == 200
    assert client.post("/auth/revoke", headers=h).status_code == 200
    assert client.post("/chat", json={"message": "oi"}, headers=h).status_code == 401
    assert verify_token(issue_token("u1")) == "u1"  # revoga o token, não o usuário


def test_legacy_signature_format_is_not_accepted():
    sig = hashlib.sha256(b"x").hexdigest()[:32]
    assert verify_token(f"u1.{sig}") is None


# --- operador ------------------------------------------------------------------------------------
def test_personal_operator_key_is_recorded_on_the_reply(monkeypatch):
    key = "chave-pessoal-da-ana-com-32-caracteres!"
    monkeypatch.setenv("OPERATOR_KEYS", f"ana:{hashlib.sha256(key.encode()).hexdigest()}")
    resp = client.post("/chat", json={"message": "Quero falar com um atendente"},
                       headers={"Authorization": f"Bearer {issue_token('u1')}"}).json()
    ticket = resp["handoff"]["ticket_id"]
    assert client.get("/handoffs", headers={"X-Operator-Token": "chave-errada"}).status_code == 403
    r = client.post(f"/handoffs/{ticket}/reply", json={"answer": "Oi, sou a Ana."},
                    headers={"X-Operator-Token": key})
    assert r.status_code == 200 and r.json()["ticket"]["answered_by"] == "ana"


def test_shared_operator_token_still_works_but_is_flagged():
    assert auth.operator_name("test-operator") == "admin"
    assert client.get("/health").json()["operator_auth"] == "shared"


def test_operator_key_cli_prints_hash_line(capsys):
    auth.main(["operator-key", "bruno"])
    out = capsys.readouterr().out
    key = out.split("chave:")[1].split()[0]
    assert f"bruno:{hashlib.sha256(key.encode()).hexdigest()}" in out


# --- rate limit compartilhado ---------------------------------------------------------------------
def test_rate_limit_is_shared_across_store_instances(monkeypatch, tmp_path):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    a, b = Store(tmp_path / "shared.db"), Store(tmp_path / "shared.db")  # dois processos, um volume
    assert [a.rate_allow("k"), b.rate_allow("k"), a.rate_allow("k"), b.rate_allow("k")] == [True, True, True, False]
    assert a.rate_allow("outra") is True


def test_rate_limit_fails_closed(monkeypatch):
    def broken(*_a, **_k):
        raise OSError("disco cheio")

    monkeypatch.setattr(Store, "_db", broken)
    assert auth.LIMITER.allow("k") is False  # contador indisponível → recusa, não libera


def test_sqlite_survives_concurrent_writers(tmp_path):
    path = tmp_path / "concurrent.db"
    Store(path)

    def write(n):
        s = Store(path)
        for i in range(25):
            s.add_message(f"c{n}", "u1", "user", f"m{i}")

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(len(Store(path).history(f"c{n}", limit=100)) for n in range(4)) == 100
    assert get_store() is not None
