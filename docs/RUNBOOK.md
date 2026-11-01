# Runbook — Getnet Support Agents

Para quem está de plantão. Métricas e SLOs em [OBSERVABILITY.md](OBSERVABILITY.md); regras em `ops/alerts.yml`.

## Incidentes ↔ alertas

| Alerta | Sintoma para o lojista | Confirmar | Responder |
|---|---|---|---|
| `LatenciaAltaDoTurno` | resposta demora | `/metrics`: `span_latency_ms{span="agent"}`; logs do `trace_id` lento: qual agente/tool | provedor lento → baixar effort do papel (`AGENT_EFFORT=low`) ou `RERANK=off`; se persistir, `LLM_PROVIDER=offline` (caminho determinístico) |
| `ErrosDoProvedorLLM` | respostas mais secas, sem rerank | `llm_errors_total` por tipo; `router_fallback_total` e `supervisor_fallback_total` subindo | o sistema já cai no determinístico; verificar chave, cota e status do provedor; sem previsão, `LLM_PROVIDER=offline` |
| `NumeroSemBase` | risco de valor/data errado | logs `numeric_ungrounded` com os números e o `trace_id`; ver a evidência do turno | corpus desatualizado → `python -m app.ingest`; prompt induzindo número → revisar `src/app/domain/challenge_prompts/`; adicionar o caso ao eval |
| `EscalonamentoAnormal` | muitos "um atendente vai responder" | `replans_total` por `reason` (`insufficient` = sem fonte; `error` = tool) | `insufficient` em massa → índice vazio/velho (reingerir); `error` → CRM/ferramenta fora; `low_confidence` → Router (ver `router_fallback_total`) |
| `SLAVencido` | lojista sem resposta humana | `GET /handoffs` (casos vencidos), `sla_escalations_total` | chamar operador de reserva; o re-escalonamento é único por caso, não repete |
| `ContadorDeLimiteIndisponivel` | 429 para todos (falha fechada) | `rate_limit_store_errors_total`; disco/volume do SQLite | liberar disco, conferir permissão do volume `appdata`; reiniciar a réplica |
| `GastoPorHoraAlto` | — | `llm_cost_usd_total`, `llm_tokens_total` por modelo; `rate_limited_total` (abuso?) | reduzir `RATE_LIMIT_PER_MIN`; trocar papel caro para modelo menor (`<PAPEL>_MODEL`); em último caso `LLM_PROVIDER=offline` |

Outros sinais sem alerta: `auth_failures_total`/`operator_auth_failures_total` subindo (força bruta: revogar com
`POST /auth/revoke`, trocar `OPERATOR_KEYS`); `poisoned_docs_dropped_total` > 0 (documento com instrução
embutida na base: revisar a fonte); `actions_expired_total` alto (lojistas não confirmam: revisar a mensagem).

## Deploy

1. `python scripts/check.py --docker --scale` verde (lint, testes, eval com gate, build, imagem sem extras,
   smoke do container, 2 réplicas). O mesmo roda no CI (`.github/workflows/ci.yml`).
2. Segredos no `.env` (nunca no compose): `AUTH_SECRET`, `OPERATOR_KEYS`, chave do LLM, `TELEGRAM_*`.
   `APP_ENV=production` recusa subir com segredo padrão.
3. `docker compose up -d --build`; conferir `curl -s localhost:8000/health` e `/metrics`.
4. Base de conhecimento nova: `python -m app.ingest`, rodar o eval offline e commitar o corpus junto.

## Rollback

- Imagem: voltar a tag/commit anterior e `docker compose up -d --build`. O SQLite fica no volume; o esquema
  é criado de forma aditiva, então a versão anterior lê o mesmo banco.
- Comportamento: flags sem rebuild, por variável de ambiente — `SUPERVISOR_REPLAN=off` (só plano inicial),
  `SUPERVISOR_PARALLEL=false`, `RETRIEVER=bm25`, `RERANK=off`, `LLM_PROVIDER=offline`.
- Corpus: `git checkout <commit> -- <diretório da base>` e reiniciar (o índice recarrega pelo carimbo dos arquivos).

## Réplicas

`docker-compose.scale.yml`: 2 réplicas da api atrás de um balanceador em Python (`ops/lb.py`), mesmo banco
SQLite em WAL num volume compartilhado. Prova: `python scripts/scale_smoke.py` — a conversa continua de uma
réplica para a outra, a mesma conversa escalada 8 vezes ao mesmo tempo abre um caso só, e uma réplica cai e o
atendimento segue. Limite: mesmo host ([ADR 0002](adr/0002-replicas-mesmo-host.md)); em vários hosts, Postgres
atrás de `Store` e rate limit/métricas em Redis/Prometheus remoto.

- Réplica caiu: o balanceador manda para a outra; `docker compose -f docker-compose.scale.yml ps` e `up -d`.
- Caso duplicado: não deve acontecer (claim atômico); se aparecer, conferir se as réplicas usam o mesmo volume.
