"""Identidade, operadores e limite de uso.

Cliente: JWT HS256 padrão (sub, iat, exp, jti, iss, aud), só biblioteca padrão. Qualquer biblioteca JWT
valida o mesmo token, e trocar por OIDC/JWKS do provedor de identidade mantém a interface
(verify_token -> user_id). Recusa: assinatura errada, `alg` diferente de HS256 (inclui "none"),
expirado (folga de LEEWAY_S), `iss`/`aud` diferentes e `jti` revogado (POST /auth/revoke).

Operador: chave pessoal; a configuração guarda só o sha256 (OPERATOR_KEYS="ana:<hash>,bruno:<hash>").
O nome entra em cada resposta e aprovação (auditoria). OPERATOR_TOKEN compartilhado continua aceito
como "admin" para demo, e o /health o sinaliza como "shared".

Uso:
  python -m app.auth u1 [--ttl 60]       → token de desenvolvimento para u1 (padrão TOKEN_TTL_MIN)
  python -m app.auth operator-key ana    → chave nova para a operadora + linha "ana:<sha256>" do OPERATOR_KEYS
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import sys
import time

from .config import (
    auth_audience,
    auth_issuer,
    auth_secret,
    operator_keys,
    operator_token,
    rate_limit_per_min,
    token_ttl_min,
)
from .observability import METRICS, log

LEEWAY_S = 30  # relógios de cliente e servidor raramente batem; 30 s é a folga usual de JWT


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(signing_input: str) -> str:
    return _b64e(hmac.new(auth_secret().encode(), signing_input.encode(), hashlib.sha256).digest())


def issue_token(user_id: str, ttl_min: int | None = None) -> str:
    now = int(time.time())
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"sub": user_id, "iat": now, "exp": now + 60 * (ttl_min or token_ttl_min()),
              "jti": secrets.token_hex(8), "iss": auth_issuer(), "aud": auth_audience()}
    body = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    return f"{header}.{body}.{_mac(f'{header}.{body}')}"


def decode_token(token: str | None) -> dict | None:
    """Claims se o token é válido agora; None em qualquer falha (sem dizer qual: não ajuda quem forja)."""
    try:
        head, body, sig = (token or "").split(".")
        if json.loads(_b64d(head)).get("alg") != "HS256":
            return None
        # bytes, não str: compare_digest com str não-ASCII levanta TypeError (500 em vez de 401)
        if not hmac.compare_digest(sig.encode(), _mac(f"{head}.{body}").encode()):
            return None
        claims = json.loads(_b64d(body))
    except (ValueError, TypeError, AttributeError):
        return None
    now = time.time()
    if not claims.get("sub") or claims.get("exp", 0) + LEEWAY_S < now or claims.get("iat", 0) - LEEWAY_S > now:
        return None
    if claims.get("iss") != auth_issuer() or claims.get("aud") != auth_audience():
        return None
    from .store import get_store  # import tardio: store não depende de auth

    if get_store().is_revoked(claims.get("jti", "")):
        return None
    return claims


def verify_token(token: str | None) -> str | None:
    claims = decode_token(token)
    return claims["sub"] if claims else None


def revoke_token(token: str) -> bool:
    claims = decode_token(token)
    if not claims:
        return False
    from .store import get_store

    get_store().revoke(claims["jti"], claims["exp"])
    return True


# --- operadores ------------------------------------------------------------------------------------
def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def operator_name(token: str | None) -> str | None:
    """Nome do operador dono da chave; "admin" para o token compartilhado; None se não autorizado."""
    if not token:
        return None
    digest = _hash(token)
    for name, expected in operator_keys().items():
        if hmac.compare_digest(digest.encode(), expected.lower().encode()):
            return name
    if hmac.compare_digest(token.encode(), operator_token().encode()):
        return "admin"
    return None


def operator_auth_mode() -> str:
    return "personal" if operator_keys() else "shared"


# --- limite de uso -----------------------------------------------------------------------------------
class RateLimiter:
    """Janela deslizante de 60 s por chave, no SQLite: vale para todos os processos que leem o mesmo volume.

    Falha fechado: se o contador não responde, recusa (429) em vez de liberar tráfego sem limite.
    """

    def allow(self, key: str) -> bool:
        from .store import get_store

        try:
            return get_store().rate_allow(key, rate_limit_per_min())
        except Exception as exc:  # noqa: BLE001 — qualquer falha do contador vira recusa, nunca liberação
            METRICS.inc("rate_limit_store_errors_total")
            log.warning("rate_limit_store_error", extra={"extra_fields": {"error": type(exc).__name__}})
            return False

    def reset(self) -> None:
        from .store import get_store

        try:
            get_store().rate_reset()
        except Exception:  # noqa: BLE001 — reset é para testes; banco ausente não é erro aqui
            pass


LIMITER = RateLimiter()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["operator-key"]:
        name = argv[1] if len(argv) > 1 else "operador"
        key = secrets.token_urlsafe(24)
        print(f"chave: {key}\n(entregue só ao operador; ela não é guardada)\n"
              f"OPERATOR_KEYS entry: {name}:{_hash(key)}")
        return 0
    ap = argparse.ArgumentParser(prog="python -m app.auth")
    ap.add_argument("user_id", nargs="?", default="u1")
    ap.add_argument("--ttl", type=int, default=None, help="minutos (padrão TOKEN_TTL_MIN)")
    args = ap.parse_args(argv)
    print(issue_token(args.user_id, args.ttl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
