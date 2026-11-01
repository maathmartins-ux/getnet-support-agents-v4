# Observabilidade — Getnet Support Agents

O que o sistema emite, onde ver e o que alerta. Implementação em `src/app/observability.py`,
`src/app/analytics.py` e `ops/alerts.yml`; resposta a incidentes em [RUNBOOK.md](RUNBOOK.md).

## SLOs (metas, não medidas de produção)

São metas de projeto para um piloto; os valores medidos no eval estão na §5 do README (gerada de `evals/results/`).

| SLO | Meta | Como medir | Alerta |
|---|---|---|---|
| Latência do turno | média de agente < 8 s em 5 min | `span_latency_ms{span="agent"}` (soma/contagem) | `LatenciaAltaDoTurno` |
| Disponibilidade do LLM | < 0,1 erro/s do provedor | `llm_errors_total` | `ErrosDoProvedorLLM` |
| Número sem base | ≤ 5 por hora | `numeric_ungrounded_total` | `NumeroSemBase` |
| Escalonamento | < 40% dos chats em 30 min | `handoffs_total` / `chat_requests_total` | `EscalonamentoAnormal` |
| SLA humano | 0 caso vencido | `sla_escalations_total` | `SLAVencido` |
| Rate limit | contador sempre disponível | `rate_limit_store_errors_total` | `ContadorDeLimiteIndisponivel` |
| Custo | ≤ US$ 5 por hora | `llm_cost_usd_total` | `GastoPorHoraAlto` |

## Métricas (`GET /metrics`, formato Prometheus)

Nomes conferidos no código (`METRICS.inc`/`observe` em `src/app/*.py`).

| Grupo | Métricas |
|---|---|
| Tráfego | `chat_requests_total`, `feedback_total`, `rate_limited_total` |
| Latência | `span_latency_ms` (por `span`: guardrail, router, agent, ...) |
| LLM e custo | `llm_tokens_total`, `llm_cost_usd_total`, `llm_errors_total`, `llm_unpriced_calls_total` |
| Orquestração | `replans_total{agent,reason,engine}`, `parallel_batches_total`, `router_fallback_total`, `supervisor_fallback_total` |
| RAG | `rerank_fallback_total`, `poisoned_docs_dropped_total` |
| Ferramentas e ações | `tool_calls_total`, `approvals_requested_total`, `approvals_decided_total`, `actions_expired_total` |
| Qualidade | `numeric_ungrounded_total`, `risk_flags_total{flag}` |
| Humano | `handoffs_total`, `handoff_notifications_total`, `handoff_replies_total`, `handoff_resumed_total`, `sla_escalations_total` |
| Segurança | `auth_failures_total`, `operator_auth_failures_total`, `guardrail_blocks_total`, `guardrail_llm_unavailable_total`, `rate_limit_store_errors_total`, `mcp_auth_failures_total`, `mcp_calls_total` |

Limite: métricas em memória do processo; com 2 réplicas cada uma expõe as suas e o Prometheus soma
(`sum(...)` nas regras de alerta).

## Alertas (`ops/alerts.yml`)

Sete regras Prometheus, validadas por `tests/test_alerts.py::test_alert_rules_are_valid_and_actionable`
(expressão válida e anotação com ação): `LatenciaAltaDoTurno`, `ErrosDoProvedorLLM`, `NumeroSemBase`,
`EscalonamentoAnormal`, `SLAVencido`, `ContadorDeLimiteIndisponivel`, `GastoPorHoraAlto`. Cada uma tem a
resposta no [RUNBOOK.md](RUNBOOK.md).

## Logs

Log JSON por linha, um `trace_id` por request, presente em todas as linhas do turno (router, cada agente,
ferramentas, grounding, handoff). Eventos úteis: `numeric_ungrounded` (com os números), `risk_flagged`
(com os rótulos), `resume_llm_failed`. PII é mascarada antes do log. Para seguir um turno:
`docker compose logs api | grep <trace_id>`; o `trace_id` também volta na resposta do `/chat`.

## Tracing (OpenTelemetry, opcional)

`OTEL_EXPORTER=console` ou `otlp` com o extra `.[otel]` instalado: o mesmo `span()` do log abre um span OTel
ligado ao `trace_id` (`tests/test_otel.py::test_console_exporter_emits_span_linked_to_trace_id`). Sem a
variável, só log JSON e `/metrics`, sem dependência extra na imagem.

## Custo por request

Cada chamada ao LLM soma tokens de entrada e saída × tabela de preço por modelo (`PRICES_PER_MTOK` em
`src/app/config.py`); a resposta do `/chat` traz `usage` (tokens e `cost_usd`) e o total vai para
`llm_cost_usd_total` (`tests/test_agents.py::test_cost_per_request_from_price_table`). Modelo sem preço na tabela
conta em `llm_unpriced_calls_total`, para o custo nunca ser subestimado em silêncio. `GET /analytics` agrega
resolução automática, custo por conversa resolvida e 👍/👎 do `/feedback`.
