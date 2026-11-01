# {{NOME DO PROJETO}}

{{Uma frase: o que o sistema faz e para quem.}}

| | |
|---|---|
| Rodar | `docker compose up -d --build` → `http://localhost:8000/docs` · console do operador em `/operator` |
| Testar | `python scripts/check.py --docker --scale` (lint, testes, eval com gate, build, imagem sem extras, smoke do container, 2 réplicas atrás de balanceador) |
| CI | {{badge/link do último run verde}} |
| Vídeo | [walkthrough na página de Releases](../../releases/tag/video) (MP4 + legendas; narração por IA a partir do roteiro do autor, telas e números reais) |

## 0. Para quem avalia em 5 minutos

| Minuto | O quê | Comando / onde |
|---|---|---|
| 1 | subir e perguntar (payload literal do enunciado) | `docker compose up -d --build` e o `curl` da §1 |
| 2 | conferir que tudo passa, do lint ao container | `python scripts/check.py --docker` (CI: link no topo) |
| 3 | números, todos gerados do `evals/results/` commitado | §5 (offline com gate, ao vivo com pass^3, juiz calibrado, replanejamento com × sem, matriz de confusão) |
| 4 | onde cada critério da régua está provado, e o limite de cada um | §3 (o `check.py` reprova prova citada que falha) |
| 5 | o que é kit e o que foi feito no relógio | §8 e `git log` |

## 1. Como rodar

```bash
cp .env.example .env                      # opcional: sem chave roda no modo offline determinístico
docker compose up -d --build
curl -s localhost:8000/health
TOKEN=$(python -m app.auth u1)            # token de desenvolvimento assinado para o usuário u1
curl -s -X POST localhost:8000/chat -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"message":"Qual o prazo de entrega? E onde está meu pedido 1002?"}'
```

- **Identidade:** `AUTH_MODE=required` (só Bearer) ou `demo` (aceita `user_id` do corpo, marca
  `identity_verified=false`, rate limit por IP). Decisão em [docs/adr/0001-auth-mode-demo.md](docs/adr/0001-auth-mode-demo.md).
- **Operador humano:** `/operator` (token digitado na página, nunca embutido), `GET /handoffs`,
  `POST /handoffs/{id}/reply` com `X-Operator-Token`. A resposta entra na conversa do cliente e o
  próximo turno já a enxerga. Opcional: Telegram (`TELEGRAM_*` no `.env`) avisa o caso e aceita
  `/responder T-xxxx texto` pelo webhook `/telegram/webhook`.
- **Ao vivo:** `powershell -File scripts/live_eval.ps1 -MaxUsd 3` (eval por split com teto de US$,
  juiz e README). Extras opcionais fora da imagem: `.[hybrid]` (busca híbrida) e `.[otel]` (OpenTelemetry).

## 2. Arquitetura

```mermaid
flowchart LR
  U[Cliente + token] --> API[/POST /chat/] --> RL[Auth demo/required + rate limit] --> G1[Guardrail regex + PII]
  G1 --> G2[Guardrail Haiku] & R[Router Haiku / regras]
  G1 --> M[(SQLite: histórico, memória, tickets, ações)]
  R --> S{Supervisor Sonnet low / tabela}
  S -->|lote paralelo| A1[faq: RAG BM25 ou híbrido] & A2[records: tools com confirmação]
  A1 & A2 -->|status ok / insufficient / error| S
  S -->|replaneja| E[escalation] --> Q[(Fila de handoff)] --> OP[/operator ou Telegram/] -->|resposta| M
```

{{Por que essa divisão: uma fonte de verdade por agente; contrato em src/app/schemas.py.}}

## 3. Cobertura da régua de avaliação

Mapa para quem avalia **lendo**: código, teste que prova e onde está o número. Os números vêm dos
blocos da §5, gerados de `evals/results/` por `scripts/fill_readme.py`. Todo caminho em crase desta
seção existe: `tests/test_evidence.py` falha se um sumir. Reproduzir tudo: `python scripts/check.py --docker`.

| Critério | O que foi feito | Código | Prova | Número | Limite conhecido |
|---|---|---|---|---|---|
| Business & Challenge Fit | {{cenários do enunciado → split `official`; KPI do negócio que o sistema move}} | `src/app/tools.py` | `evals/cases.jsonl` | §5 Offline e Ao vivo, `official` | {{o que não cobre e por quê}} |
| Orchestration | Supervisor LLM com agentes como tools, lê `insufficient`/`error` e replaneja; tabela PLANS/FALLBACK/ALLOWED/MAX_STEPS; agentes independentes em paralelo | `src/app/supervisor.py` | `tests/test_supervisor.py::test_llm_reads_insufficient_and_replans_to_escalation`, `tests/test_supervisor.py::test_llm_disallowed_transition_is_refused`, `tests/test_parallel.py::test_independent_agents_run_in_parallel_in_plan_order` | §5 Replanejamento, splits `replan` e `parallel` | {{o que não cobre e por quê}} |
| Router | Haiku com JSON schema e few-shot dinâmico (8 exemplos mais parecidos); regra por frase + k-NN de n-gramas no offline; seguimento pela última fala com assunto; teste que reprova frase do held-out copiada para regra | `src/app/router.py` | `tests/test_router.py::test_llm_failure_falls_back_to_rules`, `tests/test_router_knn.py::test_unseen_paraphrase_goes_to_right_intent`, `tests/test_router_followup.py::test_follow_up_skips_acknowledgement`, `tests/test_router_contamination.py::test_detector_catches_v3_leak` | §5 Offline, `intent_acc` | {{o que não cobre e por quê}} |
| Context, Knowledge & RAG | BM25 e híbrido (RRF) atrás de `RETRIEVER`, com **rerank do top-20 pelo Haiku** quando há LLM; "sem fonte, sem afirmação"; **todo número da resposta conferido contra fonte, tool e pergunta** (sem LLM; `unsupported_numbers` na resposta) | `src/app/rag.py`, `src/app/grounding.py` | `tests/test_rag.py::test_compare_retrievers_reports_both`, `tests/test_rerank.py::test_rerank_follows_the_llm_order_and_keeps_bm25_scores`, `tests/test_rerank_eval.py::test_compare_adds_a_rerank_row_only_when_asked`, `tests/test_agents.py::test_faq_llm_not_in_sources_is_insufficient`, `tests/test_grounding.py::test_pipeline_flags_invented_number_and_counts_it`, `evals/retrieval.jsonl` | §5 Offline, Recall@3, MRR e `numbers_acc` | {{o que não cobre e por quê}} |
| Tool & Action | JSON Schema, identidade do servidor, escrita com confirmação do dono; **risco alto com aprovação de operador (quatro olhos)**; ação pendente expira (TTL); execução única entre réplicas; **servidor MCP em `/mcp`** (ask, search_knowledge, crm_tool; JWT obrigatório) | `src/app/actions.py`, `src/app/tools.py`, `src/app/mcp_server.py` | `tests/test_mcp.py::test_crm_tool_identity_comes_from_token_and_write_needs_confirmation`, `tests/test_mcp.py::test_anonymous_gets_401_before_the_protocol`, `tests/test_agents.py::test_tool_identity_is_server_side`, `tests/test_hitl.py::test_high_risk_action_needs_owner_then_operator_and_runs_once`, `tests/test_hitl.py::test_pending_action_expires`, `tests/test_memory_and_handoff.py::test_claim_is_atomic` | §5 Offline, `tools_acc` | {{o que não cobre e por quê}} |
| Security & Identity | JWT HS256 com `exp`/`iss`/`aud`/`jti` e revogação; operador com chave pessoal (auditoria); rate limit no SQLite que falha fechado; `AUTH_MODE` required/demo; guardrails regex + Haiku com desofuscação e injeção indireta; PII mascarada na entrada, no log e na saída | `src/app/auth.py`, `src/app/guardrails.py` | `tests/test_identity.py::test_expired_token_is_rejected`, `tests/test_identity.py::test_revoked_token_is_rejected_via_api`, `tests/test_identity.py::test_rate_limit_fails_closed`, `tests/test_guardrails.py::test_obfuscated_injection_is_caught`, `tests/test_guardrails.py::test_poisoned_document_is_dropped_at_load`, `docs/adr/0001-auth-mode-demo.md` | §5 Offline, split `guardrail` | {{o que não cobre e por quê}} |
| Memory, State & Resilience | Histórico em SQLite (WAL, vários processos) com **resumo rolante** além da janela; memória de longo prazo por usuário; queda do LLM cai no determinístico | `src/app/store.py`, `src/app/pipeline.py` | `tests/test_memory_and_handoff.py::test_history_survives_restart`, `tests/test_analytics.py::test_rolling_digest_keeps_early_facts_beyond_the_window`, `tests/test_identity.py::test_sqlite_survives_concurrent_writers`, `tests/test_agents.py::test_records_llm_failure_uses_deterministic_path`, `tests/test_runtime_store.py::test_two_replicas_escalating_the_same_conversation_open_one_ticket`, `scripts/scale_smoke.py` (conversa continua entre 2 réplicas, réplica cai e o atendimento segue) | §5 Offline, split `memory` | {{o que não cobre e por quê}} |
| Human Oversight | Fila persistida e console; **a resposta do operador retoma a conversa na hora**; aprovação de ação de risco; SLA com re-escalonamento único; Telegram ida e volta | `src/app/resume.py`, `src/app/api.py`, `src/app/notify.py` | `tests/test_hitl.py::test_operator_reply_resumes_the_conversation_immediately`, `tests/test_hitl.py::test_overdue_case_is_escalated_once`, `tests/test_hitl.py::test_two_simultaneous_replies_only_one_wins`, `tests/test_notify.py::test_reply_from_telegram_resumes_conversation` | §5 Offline, `handoff_acc` | {{o que não cobre e por quê}} |
| Evaluation & Quality | Gate offline no CI; ao vivo por split com teto de US$; `--repeat N` (pass^k, instáveis); juiz calibrado (kappa, TPR/TNR); checagem numérica sem LLM; matriz de falhas; limiar do portão por medida | `evals/run_evals.py`, `evals/judge.py`, `src/app/grounding.py` | `tests/test_evals.py::test_gate_passes_in_process`, `tests/test_evals.py::test_calibration_reports_tpr_tnr`, `tests/test_evals.py::test_repeat_reports_pass_hat_k_and_unstable_cases`, `tests/test_rag.py::test_gate_threshold_is_chosen_by_measurement`, `tests/test_eval_isolation.py::test_each_case_starts_without_memory_of_other_cases`, `tests/test_eval_judge_evidence.py::test_live_rows_record_tool_calls_sources_and_evidence`, `tests/test_eval_language_ci.py::test_detects_language_of_the_text`, `evals/judge_calibration.jsonl` | §5 Offline, Ao vivo e Juiz | {{o que não cobre e por quê}} |
| Observability, Reliability & Cost | Log JSON com `trace_id` e status de cada agente; US$ e tokens por request; `/metrics`; **alertas Prometheus provisionados**; `/analytics` (resolução, custo por resolvido, 👍/👎); OTel por env | `src/app/observability.py`, `src/app/analytics.py`, `ops/alerts.yml` | `tests/test_agents.py::test_cost_per_request_from_price_table`, `tests/test_alerts.py::test_alert_rules_are_valid_and_actionable`, `tests/test_analytics.py::test_every_request_is_logged_and_feedback_is_owned`, `tests/test_otel.py::test_console_exporter_emits_span_linked_to_trace_id` | §5 Ao vivo, `cost_usd` e p95 | {{o que não cobre e por quê}} |
| Production Engineering | Dockerfile multi-stage não-root, base fixada por digest com Dependabot, extra opcional embutido (`EXTRAS=hybrid`), lockfiles, smoke do container e **2 réplicas atrás de balanceador** no CI; scripts em bash e PowerShell | `Dockerfile` | `scripts/check.py`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `requirements.lock`, `requirements-hybrid.lock`, `scripts/live_eval.sh` | link do CI no topo | {{o que não cobre e por quê}} |
| Engineering Judgment | Decisões com a alternativa descartada; limitações declaradas | `docs/adr/0001-auth-mode-demo.md` | §4 e §9 deste README | — | {{o que não cobre e por quê}} |

## 4. Decisões e trade-offs

| Decisão | Alternativa descartada | Motivo |
|---|---|---|
| Supervisor LLM com tabela determinística como fallback | só LLM / só tabela | replan inteligente ao vivo; CI e falha do provedor seguem determinísticos |
| Agentes independentes em threads | asyncio | os agentes fazem I/O síncrono (SQLite, SDK); threads cortam o p95 sem reescrever a pilha |
| Effort por papel (supervisor low, agentes medium) | um effort global | o supervisor só escolhe o próximo passo; quem redige com fonte precisa de mais |
| BM25 padrão, híbrido atrás de flag | embeddings desde o início | dependência pesada só entra se Recall@3/MRR mostrarem ganho |
| SQLite atrás de `Store` | Postgres/Redis | sobe com um comando; a troca para réplicas fica isolada num módulo |
| {{...}} | {{...}} | {{...}} |

## 5. Avaliação

Números gerados por `python scripts/fill_readme.py` a partir de `evals/results/`.

### Offline (gate do CI)

<!-- eval:begin -->
<!-- eval:end -->

### Ao vivo (LLM real, por split)

<!-- eval-live:begin -->
<!-- eval-live:end -->

### Juiz LLM sobre as respostas ao vivo

<!-- judge:begin -->
<!-- judge:end -->

### Replanejamento

<!-- replan:begin -->
<!-- replan:end -->

## 6. Spec e rastreabilidade

Spec em [docs/SPEC.md](docs/SPEC.md), commitada antes do código. ADRs em [docs/adr/](docs/adr/).

## 7. Linha do tempo (git log)

<!-- timeline:begin -->
<!-- timeline:end -->

## 8. Scaffold declarado (kit) e o que foi feito no relógio

A infraestrutura genérica vem de um kit próprio, preparado antes do desafio e declarado no primeiro
commit (`chore: scaffold`):
- FastAPI, Docker, CI e lockfiles;
- runner de eval (offline, ao vivo por split com teto de US$, `--repeat` com pass^k, calibração do
  portão), checagem numérica e juiz calibrado (kappa, TPR/TNR);
- identidade JWT com expiração e revogação, operador com chave pessoal, rate limit compartilhado;
- memória com resumo rolante, fila humana com retomada imediata, aprovação de quatro olhos, SLA;
- supervisor que replaneja com agentes em paralelo, busca BM25/híbrida, guardrails com desofuscação;
- logs, métricas, alertas provisionados, custo, analytics e OpenTelemetry opcional.

**Por que um kit.** É a prática de engenharia de implantação (forward deployed): bibliotecas comuns e
automação do trabalho repetível, para o tempo do cliente ir para o problema dele (a16z, "Trading Margin
for Moat", 04/06/2025: "Build common software libraries" e "Build/buy tools to automate"). O que é
específico do desafio (spec, base de conhecimento, ferramentas do domínio, casos de avaliação, prompts,
regras do router e do supervisor) foi escrito no relógio. A medida, tirada do git:

<!-- kit:begin -->
<!-- kit:end -->

## 9. Limitações e próximos passos

- {{o que é simulado}}
- Rate limit, métricas e cache em memória do processo; com réplicas, mover para Redis e Prometheus remoto.
- {{o que ficou de fora e por quê; o que não rodou no relógio}}
