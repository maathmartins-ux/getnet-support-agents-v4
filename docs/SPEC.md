# Spec — {{NOME}}

Commitada antes de qualquer código. Congelada (OWNERSHIP.json): mudança aqui é decisão da sessão principal.

## Problema
{{2–3 frases a partir do enunciado, sem parafrasear demais.}}

## Requisitos

| ID | Requisito (do enunciado) | Módulo | Teste / eval |
|---|---|---|---|
| FR-01 | {{...}} | `src/app/...` | `tests/...` |
| NFR-01 | Sobe com um comando (Docker) | `Dockerfile` | `scripts/check.py --docker` |
| NFR-02 | Funciona sem chave de LLM (modo offline) | `config.py` | CI |
| NFR-03 | Identidade: `AUTH_MODE` {{demo ou required}} (ADR 0001) | `api.py` | `tests/test_auth_modes.py` |
| NFR-03 | Identidade: `AUTH_MODE` {{demo ou required}} (ADR 0001) | `api.py` | `tests/test_auth_modes.py` |

## Cenários de aceite (copiados do enunciado, um por linha)

| ID | Mensagem | Esperado (intent / rota / ferramenta / handoff) |
|---|---|---|
| AC-01 | {{...}} | {{...}} |

Cada AC vira um caso `official-NN` em `evals/cases.jsonl`.

## Fora de escopo
- {{...}}

## Divisão em forks

Ver OWNERSHIP.json. Contrato: `src/app/schemas.py` (inclui `RouterDecision` e `AgentName`), assinatura de
`pipeline.handle(req, user_id, identity_verified)` e status `ok|insufficient|error` dos agentes.
