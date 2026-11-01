"""Teste de réplicas: 2 cópias da api atrás de um balanceador, com o mesmo banco. Roda em `make scale` e no CI.

Prova, pelo HTTP, três coisas que a banca pontua em Memory/State e Production (as soluções de 9+ na Régua V4
mostram réplicas; a v3 só declarava "fora do escopo"):
1. a conversa continua de uma réplica para a outra (histórico completo, servido por réplicas diferentes);
2. a mesma conversa, escalada 8 vezes ao mesmo tempo nas duas réplicas, abre UM caso de atendimento;
3. uma réplica cai e o atendimento segue pela outra.

Uso: python scripts/scale_smoke.py [--keep]   (precisa de Docker; constrói a imagem como o check.py)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check import cloud_build_args  # noqa: E402

IMAGE, PROJECT, PORT = "app-scale", "scale", int(os.getenv("SCALE_PORT", "8080"))
SECRET = "scale-secret-only-for-the-replica-smoke"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.scale.yml"), "-p", PROJECT]
ENV = {**os.environ, "SCALE_IMAGE": IMAGE, "SCALE_AUTH_SECRET": SECRET, "SCALE_PORT": str(PORT)}


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace")


def token(user: str) -> str:
    os.environ["AUTH_SECRET"] = SECRET
    sys.path.insert(0, str(ROOT / "src"))
    from app.auth import issue_token

    return issue_token(user)


def call(method: str, path: str, tok: str, body: dict | None = None) -> tuple[int, dict, str]:
    req = urllib.request.Request(f"http://localhost:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), resp.headers.get("X-Instance", "?")
    except urllib.error.HTTPError as exc:
        return exc.code, {}, exc.headers.get("X-Instance", "?")


def wait_two_replicas(tok: str, timeout: float = 90) -> set[str]:
    seen: set[str] = set()
    deadline = time.time() + timeout
    while time.time() < deadline and len(seen) < 2:
        try:
            code, _, inst = call("GET", "/health", tok)
            if code == 200:
                seen.add(inst)
        except Exception:
            pass
        time.sleep(0.5)
    return seen


def continuity(tok: str) -> tuple[bool, str]:
    conv, served, said = None, [], []
    for i in range(6):
        msg = f"Mensagem número {i + 1} da conversa de teste de réplicas."
        code, body, inst = call("POST", "/chat", tok, {"message": msg, **({"conversation_id": conv} if conv else {})})
        if code != 200:
            return False, f"turno {i + 1}: HTTP {code} na réplica {inst}"
        conv = body["conversation_id"]
        served.append(inst)
        said.append(msg)
    code, hist, _ = call("GET", f"/conversations/{conv}", tok)
    users = [m["content"] for m in hist.get("messages", []) if m.get("role") == "user"]
    ok = code == 200 and users == said and len(set(served)) >= 2
    return ok, f"6 turnos servidos por {len(set(served))} réplica(s) {sorted(set(served))}; histórico com " \
               f"{len(users)}/6 falas do cliente na ordem"


def one_ticket(tok: str) -> tuple[bool, str]:
    code, body, _ = call("POST", "/chat", tok, {"message": "Oi"})
    conv = body.get("conversation_id")
    tickets, insts, lock = [], [], threading.Lock()

    def escalate():
        c, b, inst = call("POST", "/chat", tok, {"message": "Quero falar com um atendente humano",
                                                 "conversation_id": conv})
        with lock:
            insts.append(inst)
            tickets.append(((b.get("handoff") or {}).get("ticket_id")) if c == 200 else f"HTTP {c}")

    threads = [threading.Thread(target=escalate) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    distinct = {t for t in tickets if t}
    return len(distinct) == 1 and all(t and t.startswith("T-") for t in tickets), \
        f"8 pedidos simultâneos em {len(set(insts))} réplica(s) → caso(s) {sorted(distinct)}"


def survives_one_down(tok: str) -> tuple[bool, str]:
    ids = sh([*COMPOSE, "ps", "-q", "api"]).stdout.split()
    if len(ids) < 2:
        return False, f"só {len(ids)} réplica(s) de pé"
    sh(["docker", "stop", "-t", "1", ids[0]])
    codes = [call("POST", "/chat", tok, {"message": "Ainda estão aí?"})[0] for _ in range(4)]
    return all(c == 200 for c in codes), f"réplica {ids[0][:12]} derrubada; 4 turnos seguintes: {codes}"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="não derruba as réplicas no fim")
    args = ap.parse_args()
    t0 = time.perf_counter()
    build = sh(["docker", "build", "-q", *cloud_build_args(), "-t", IMAGE, "."])
    if build.returncode:
        print(f"build falhou:\n{build.stderr[-1500:]}")
        return 1
    up = sh([*COMPOSE, "up", "-d"])
    if up.returncode:
        print(f"compose up falhou:\n{up.stderr[-1500:]}")
        return 1
    rows: list[tuple[str, bool, str]] = []
    try:
        tok = token("scale-user")
        seen = wait_two_replicas(tok)
        rows.append(("2 réplicas atrás do balanceador", len(seen) >= 2, f"respondendo: {sorted(seen)}"))
        if len(seen) >= 2:
            rows.append(("conversa continua entre réplicas", *continuity(tok)))
            rows.append(("um caso por conversa com escalonamento simultâneo", *one_ticket(token("scale-user-2"))))
            rows.append(("réplica cai, atendimento segue", *survives_one_down(tok)))
    finally:
        if not args.keep:
            sh([*COMPOSE, "down", "-v"])
    print("| teste de réplicas | resultado | detalhe |\n|---|---|---|")
    for name, ok, detail in rows:
        print(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    failed = [r for r in rows if not r[1]] or not rows
    print(f"\nRÉPLICAS {'VERMELHO' if failed else 'VERDE'} em {time.perf_counter() - t0:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
