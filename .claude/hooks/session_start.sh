#!/usr/bin/env bash
# SessionStart: deixa a sessão da NUVEM pronta para rodar check, eval e Docker sem ninguém pedir.
# Só age em sessão remota (claude.ai/code); no PC não faz nada. Idempotente: segunda vez é instantâneo.
# Kit v3.7 (31/10/2026): a tentativa roda 100% na nuvem; cada sessão-irmã nasce de um clone limpo.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"
stamp=".venv/.kit-ready-$(sha256sum requirements-dev.lock pyproject.toml | sha256sum | cut -c1-12)"
if [ ! -f "$stamp" ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements-dev.lock
  .venv/bin/pip install -q --no-deps -e .
  touch "$stamp"
fi
# Docker: na nuvem o cliente vem instalado, mas o daemon começa parado (check --docker e smoke do container)
if command -v dockerd >/dev/null && ! docker info >/dev/null 2>&1; then
  (dockerd > /tmp/dockerd.log 2>&1 &)
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi
# o venv vale para todos os comandos da sessão
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
  echo "export VIRTUAL_ENV=\"$PWD/.venv\"" >> "$CLAUDE_ENV_FILE"
fi
docker_state=$(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo "parado")
echo "kit pronto: $(.venv/bin/python --version), docker $docker_state, branch $(git branch --show-current)"
