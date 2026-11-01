"""Pré-voo do AMBIENTE da nuvem: confere, em segundos e ANTES do relógio, tudo o que a tentativa vai precisar.

Uso (numa sessão da nuvem, no repositório do kit):
  python scripts/cloud_probe.py --site https://site.da-empresa.com.br/
  python scripts/cloud_probe.py --site https://a.com/ --site https://b.com/ajuda/

Confere:
- chave do LLM do APP (APP_ANTHROPIC_API_KEY; na nuvem ANTHROPIC_API_KEY é do Claude Code) presente e aceita pela
  API (GET /v1/models, sem custo), SEM imprimir o valor;
- rede: API da Anthropic, PyPI, Docker Hub e cada --site (o site da empresa, para a ingestão do RAG);
- Docker: o daemon responde (o check --docker e o smoke do container rodam na nuvem);
- máquina: núcleos, RAM e disco livres desta sessão;
- git: a branch atual e se o remoto aceita leitura.

Kit v3.7 (31/10/2026): a sessão de auditoria da v3 rodava num ambiente de rede LIMITADA que recusava o site da
Getnet. Com a tentativa toda na nuvem, a frente K não conseguiria ingerir o corpus. Isso se resolve nas
configurações do ambiente (acesso de rede e variável da chave), nunca no relógio.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

CORE = {"API da Anthropic": "https://api.anthropic.com/v1/messages", "PyPI": "https://pypi.org/simple/fastapi/",
        "Docker Hub": "https://registry-1.docker.io/v2/"}


def reach(url: str) -> tuple[bool, str]:
    """Qualquer resposta HTTP (até 401/404/405) prova que a rede chega; recusa do proxy ou timeout, não."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "kit-cloud-probe"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and "proxy" in str(exc.headers.get("Server", "")).lower():
            return False, "recusado pelo proxy do ambiente (403)"
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # recusa do túnel, DNS, timeout
        return False, type(exc).__name__ + (f": {exc.reason}" if getattr(exc, "reason", None) else "")


def shape(key: str) -> str:
    """Forma da chave SEM revelá-la: o marcador público de tipo (o trecho entre `sk-ant-` e o próximo traço, ex.
    `api03`, `admin01`, `oat01`), o tamanho e sujeira comum (espaço, aspas, quebra de linha)."""
    import re

    clean = key.strip().strip("\"'").strip()
    m = re.match(r"sk-ant-([a-z]+[0-9]*)-", clean)
    kind = f"tipo `{m.group(1)}`" if m else ("começa com sk-ant- sem marcador de tipo" if clean.startswith("sk-ant-")
                                             else "não começa com sk-ant-")
    dirt = [n for n, bad in (("espaço nas pontas", key != key.strip()), ("aspas", '"' in key or "'" in key),
                             ("quebra de linha", "\n" in key or "\r" in key)) if bad]
    # 4 primeiros caracteres: numa chave real são sempre "sk-a" (público). Outra coisa = valor trocado ou cortado.
    head = clean[:4] if len(clean) > 40 else "?"
    alphabet = "".join(sorted({("a" if c.isalpha() else "9" if c.isdigit() else c) for c in clean}))[:12]
    return (f"{kind}, {len(clean)} caracteres, começa com {head!r}, alfabeto {alphabet!r}"
            + (f", com {', '.join(dirt)}" if dirt else ""))


def check_key(key: str, name: str, raw: str | None = None) -> tuple[bool, str]:
    """A chave abre a API pública? GET /v1/models não gasta crédito. Nunca imprime a chave."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=1",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True, f"{name} presente ({shape(raw or key)}) e aceita pela API (HTTP {resp.status})"
    except urllib.error.HTTPError as exc:
        why = {401: "recusada (chave inválida ou revogada)", 403: "sem permissão"}.get(exc.code, f"HTTP {exc.code}")
        try:  # o corpo do erro da API não contém a chave: tipo e mensagem dizem o motivo exato
            err = json.loads(exc.read().decode("utf-8", "replace")).get("error", {})
            detail = f" [{err.get('type', '?')}: {str(err.get('message', ''))[:160]}]"
        except Exception:
            detail = ""
        return False, (f"{name} presente ({shape(raw or key)}), mas a API respondeu: {why}{detail}. "
                       "Crie outra em console.anthropic.com → API Keys")
    except Exception as exc:
        return False, f"{name} presente, mas a API não respondeu ({type(exc).__name__})"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", action="append", default=[], help="site da empresa do desafio (repetível)")
    ap.add_argument("--chave-no-pc", action="store_true",
                    help="eval ao vivo e juiz rodam no PC (scripts/pc_live.ps1): a chave não é exigida na nuvem")
    args = ap.parse_args()
    rows: list[tuple[str, bool, str]] = []

    # Na nuvem o Claude Code reserva ANTHROPIC_API_KEY para si: o app lê APP_ANTHROPIC_API_KEY (config.llm_api_key)
    name = next((n for n in ("APP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY") if os.getenv(n)), None)
    key = os.getenv(name, "") if name else ""
    if args.chave_no_pc:
        rows.append(("chave do LLM do app", True, "não exigida na nuvem: eval ao vivo e juiz rodam no PC "
                     "(scripts/pc_live.ps1 -Preparar testa a chave lá)"))
    elif not key:
        rows.append(("chave do LLM do app", False, "ausente: nas variáveis do ambiente da nuvem, crie a linha "
                     "APP_ANTHROPIC_API_KEY=sk-ant-api... (o nome ANTHROPIC_API_KEY é reservado ao Claude Code)"))
    else:
        clean = key.strip().strip("\"'").strip()  # o app limpa igual (config.llm_api_key)
        rows.append(("chave do LLM do app", *check_key(clean, name, key)))
    for name, url in {**CORE, **{f"site {u}": u for u in args.site}}.items():
        ok, detail = reach(url)
        hint = "" if ok else " → libere o domínio em Network access do ambiente (Allowed domains) ou use Full"
        rows.append((f"rede: {name}", ok, detail + hint))
    if not args.site:
        rows.append(("rede: site da empresa", False, "não testado: rode de novo com --site <url do enunciado>"))

    docker = shutil.which("docker")

    def docker_info():
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True,
                              timeout=20) if docker else None

    info = docker_info()
    if docker and info.returncode != 0 and os.getenv("CLAUDE_CODE_REMOTE") == "true" and shutil.which("dockerd"):
        # na nuvem o daemon começa parado; o gancho de abertura sobe, mas se não subiu, sobe aqui (até 30 s)
        subprocess.Popen(["dockerd"], stdout=open("/tmp/dockerd.log", "ab"), stderr=subprocess.STDOUT)
        for _ in range(30):
            time.sleep(1)
            info = docker_info()
            if info.returncode == 0:
                break
    rows.append(("docker", bool(info and info.returncode == 0),
                 f"servidor {info.stdout.strip()}" if info and info.returncode == 0 else "daemon não responde"))

    cpus = os.cpu_count() or 0
    ram = None
    try:
        for line in open("/proc/meminfo", encoding="utf-8"):
            if line.startswith("MemAvailable:"):
                ram = int(line.split()[1]) / 2**20
    except OSError:
        pass
    disk = shutil.disk_usage(".").free / 2**30
    rows.append(("máquina", cpus >= 2 and disk >= 3, f"{cpus} núcleos, {ram or 0:.1f} GB RAM livre, "
                 f"{disk:.1f} GB de disco livre (o df pode enganar: vale a cota da sessão)"))

    branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    remote = subprocess.run(["git", "ls-remote", "--heads", "origin"], capture_output=True, text=True, timeout=30)
    rows.append(("git", remote.returncode == 0, f"branch {branch or '(destacada)'}; remoto "
                 + ("acessível" if remote.returncode == 0 else remote.stderr.strip()[-160:])))

    print("| item | resultado | detalhe |\n|---|---|---|")
    for name, ok, detail in rows:
        print(f"| {name} | {'OK' if ok else 'FALTA'} | {detail} |")
    missing = [r for r in rows if not r[1]]
    print("\nAMBIENTE " + ("PRONTO para a tentativa." if not missing else
                           f"COM {len(missing)} PENDÊNCIA(S): resolva antes de começar o relógio."))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
