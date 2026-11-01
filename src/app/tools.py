"""Registro de ferramentas: lido do DOMÍNIO ativo (app/domain/<ativo>.py, nome `TOOLS`).

O desafio nunca edita este arquivo: as tools do desafio moram em `app/domain/challenge.py`. A mecânica
(identidade do servidor, confirmação do dono, aprovação do operador, TTL, execução única) está em `actions.py`.
Contrato de cada tool: {"description", "input_schema" (JSON Schema), "fn", "write", "risk"}; `fn` recebe os
argumentos do schema + `user_id` (sempre o autenticado, injetado pelo servidor) e devolve {ok, data} ou
{ok: False, error}. Mesma resposta para registro inexistente e de outro dono.
"""
from __future__ import annotations

from . import domain


def registry() -> dict[str, dict]:
    return domain.active().TOOLS


def __getattr__(attr: str):
    # compatibilidade: `tools.TOOLS` e `tools.records()` refletem o domínio ativo
    if attr == "TOOLS":
        return registry()
    if attr == "records":
        return domain.active().records
    raise AttributeError(attr)
