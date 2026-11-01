# Arquitetura — Getnet Support Agents

Visão de engenharia do que existe no código. Contrato, requisitos e cenários estão em [SPEC.md](SPEC.md);
decisões em [adr/](adr/). Cada caixa dos diagramas aponta o arquivo que a implementa.

## 1. Fluxo de agentes num turno

```mermaid
flowchart TD
  U["Lojista: POST /chat {message, user_id}<br/>src/app/api.py"] --> AU["Auth required/demo + rate limit<br/>src/app/auth.py"]
  AU --> G1["Guardrail regex + desofuscação + PII<br/>src/app/guardrails.py"]
  G1 -->|blocked| REF["Recusa padrão"]
  G1 --> G2["Guardrail semântico (Haiku, opcional)<br/>src/app/guardrails.py"]
  G2 --> R["Router: intent, idioma, confiança, plano<br/>src/app/router.py + domain/challenge/flow.py"]
  R --> S{"Supervisor: LLM com agentes como tools<br/>ou tabela FALLBACK/ALLOWED/MAX_STEPS<br/>src/app/supervisor.py"}
  S -->|lote paralelo| K["Knowledge Agent (faq)<br/>RAG + web p/ general<br/>src/app/agents.py, src/app/rag.py"]
  S -->|lote paralelo| C["Customer Support Agent (records)<br/>6 tools sobre o CRM<br/>src/app/agents.py, domain/challenge/tools.py"]
  C --> ACT["Escrita: pending_action, confirmação, quatro olhos<br/>src/app/actions.py"]
  K -->|ok / insufficient / error| S
  C -->|ok / insufficient / error| S
  S -->|replaneja| E["Human Escalation Agent (escalation)<br/>src/app/agents.py"]
  E --> Q[("Fila de handoff, SQLite WAL<br/>src/app/store.py")]
  S --> GR["Checagem numérica + risk flags (sem LLM)<br/>src/app/grounding.py"]
  GR --> P["Resposta JSON + histórico + métricas<br/>src/app/pipeline.py, src/app/observability.py"]
```

Exemplos dos cenários: AC-03 (`support`) → Customer Support → `get_settlements`. AC-05 (`troubleshoot`) →
Customer Support (`get_terminal_status`) e depois Knowledge com o estado da maquininha (`DEPENDS`). AC-02
(`general`) → Knowledge com busca web, nunca escala (`NO_ESCALATION`). Pergunta sem fonte → Knowledge devolve
`insufficient` → supervisor replaneja para Human Escalation.

## 2. RAG: ingestão → armazenamento → busca → geração

```mermaid
flowchart LR
  SRC["data/sources.json<br/>www.getnet.net/en + site.getnet.com.br"] --> ING["python -m app.ingest<br/>src/app/ingest.py<br/>allowed_hosts, limpa HTML, chunk"]
  ING --> KB[("Trechos JSONL<br/>título, URL, data de coleta")]
  KB --> LOAD["Carga + descarte de doc envenenado<br/>src/app/rag.py, src/app/guardrails.py"]
  Q["Pergunta"] --> GL["Glossário pt↔en<br/>data/glossary.json"]
  GL --> BM["BM25 (título peso 2)<br/>src/app/rag.py"]
  LOAD --> BM
  BM -->|RETRIEVER=hybrid| HY["+ embeddings por RRF<br/>extra .[hybrid]"]
  BM -->|RERANK auto + LLM| RR["Rerank top-20 (Haiku)<br/>RerankRetriever"]
  HY --> RR
  RR --> GATE{"score ≥ COVERAGE_MIN_SCORE?"}
  BM --> GATE
  GATE -->|não| INS["insufficient → supervisor escala"]
  GATE -->|sim| GEN["Redação só com os trechos, citando [n]<br/>src/app/agents.py (faq)"]
  GEN --> NUM["Número conferido contra fonte<br/>src/app/grounding.py"]
```

## 3. HITL: handoff → operador → retomada

```mermaid
sequenceDiagram
  participant L as Lojista
  participant API as api.py / pipeline.py
  participant SUP as supervisor.py
  participant ESC as escalation (agents.py)
  participant DB as store.py (SQLite)
  participant OP as Operador (/operator ou Telegram, notify.py)
  L->>API: pergunta sem fonte / pede humano / ação high
  API->>SUP: plano
  SUP->>ESC: replaneja (insufficient, error, low_confidence)
  ESC->>DB: abre UM caso por conversa (motivo, prioridade, SLA)
  ESC-->>L: "um atendente vai responder" + ticket
  DB-->>OP: GET /handoffs (e aviso no Telegram, se configurado)
  OP->>API: POST /handoffs/{id}/reply (X-Operator-Token pessoal)
  API->>DB: claim atômico (duas respostas: só uma vence)
  API->>API: resume_after_human (resume.py): LLM redige no idioma, ou texto literal
  API->>DB: grava a resposta na conversa do lojista
  L->>API: próximo turno já enxerga a resposta do operador
  Note over API,DB: Ação high (antecipação, contestação): dono confirma → awaiting_operator → operador aprova → executa uma vez (actions.py)
  Note over DB: SLA vencido → re-escalonado uma vez (sla_escalations_total)
```

## 4. Componentes

| Componente | Arquivo | Responsabilidade | LLM |
|---|---|---|---|
| API HTTP | `src/app/api.py` | `/chat`, `/actions/*`, `/handoffs/*`, `/operator`, `/feedback`, `/analytics`, `/health`, `/metrics`, `/telegram/webhook` | não |
| Identidade | `src/app/auth.py` | JWT HS256 (`sub`, `exp`, `iss`, `aud`, `jti`), revogação, `AUTH_MODE` required/demo, chave por operador | não |
| Guardrails | `src/app/guardrails.py` | regex, desofuscação, PII, injeção indireta; camada semântica | Haiku (opcional) |
| Router | `src/app/router.py` + `src/app/domain/challenge/flow.py` | intent, idioma, confiança, plano; regras + k-NN offline | Haiku |
| Supervisor | `src/app/supervisor.py` | executa e replaneja dentro de `ALLOWED`/`MAX_STEPS`, lotes paralelos | Sonnet (effort low) |
| Agentes | `src/app/agents.py` | Knowledge (`faq`), Customer Support (`records`), Human Escalation (`escalation`) | Sonnet (effort medium) |
| RAG | `src/app/rag.py`, `src/app/ingest.py` | ingestão, BM25/híbrido, rerank, Recall@k/MRR | rerank (Haiku) |
| Ferramentas | `src/app/tools.py`, `src/app/domain/challenge/tools.py` | 6 tools do CRM fictício, JSON Schema | não |
| Ações | `src/app/actions.py` | `pending_action`, TTL, confirmação, quatro olhos, execução única | não |
| Grounding | `src/app/grounding.py` | número sem base e flags de risco | não |
| Estado | `src/app/store.py` | SQLite WAL: histórico, resumo rolante, memória, tickets, ações, rate limit | não |
| Retomada | `src/app/resume.py` | resposta do operador volta à conversa | Sonnet (opcional) |
| Notificação | `src/app/notify.py` | Telegram ida e volta | não |
| MCP | `src/app/mcp_server.py` | `/mcp` com `ask`, `search_knowledge`, `crm_tool` (JWT obrigatório) | — |
| Observabilidade | `src/app/observability.py`, `src/app/analytics.py` | log JSON com `trace_id`, `/metrics`, custo, OTel opcional, analytics | não |
| Produção | `Dockerfile`, `docker-compose.yml`, `docker-compose.scale.yml`, `ops/lb.py`, `ops/alerts.yml` | imagem não-root, 2 réplicas + balanceador, alertas | — |

Modelos por papel (`src/app/config.py`, sobrescrevíveis por `<PAPEL>_MODEL`/`<PAPEL>_EFFORT`): router e guardrail
Haiku (low), supervisor Sonnet (low), agentes Sonnet (medium), juiz do eval Opus (medium).
