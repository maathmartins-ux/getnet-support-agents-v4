# Spec — Getnet Multi-Agent Support System (v4)

Commitada antes de qualquer código de domínio; congelada (OWNERSHIP.json). Enunciado: "AI Hardcore Engineer —
Multi-Agent Support System" (cenários literais abaixo).

## Problema

Lojistas da Getnet mandam mensagens livres (pt ou en) para um único `POST /chat` com `{"message","user_id"}`. Um
sistema de agentes cooperantes decide quem atende, busca a informação certa (site oficial da Getnet com RAG, web
para assuntos gerais, dados do próprio lojista por ferramentas) e devolve JSON útil, com escalonamento humano quando
não há confiança. Métrica de negócio: resolução automática sem número inventado.

## Agentes (código → papel do enunciado)

| Código (`AgentName`) | Papel | O que faz |
|---|---|---|
| router (`router.py`) | Agent 1 — Router | intenção, plano inicial, idioma, confiança; entrada de toda mensagem |
| supervisor (`supervisor.py`) | Router: "manages the workflow and data flow" | executa o plano, lê `ok/insufficient/error` de cada agente e **replaneja** dentro de `ALLOWED`/`MAX_STEPS` |
| `faq` | Agent 2 — **Knowledge Agent** | RAG sobre o site da Getnet (`www.getnet.net/en`, a fonte indicada no enunciado, e `site.getnet.com.br`, onde estão Get Clássica, Get Smart, crediário, Link de Pagamento) + busca web para `general` |
| `records` | Agent 3 — **Customer Support Agent** | dados do lojista autenticado por 6 ferramentas (3 leitura, 3 escrita com confirmação) |
| `escalation` | Agent 4 (bônus) — **Human Escalation Agent** | caso persistido, fila `/operator`, resposta humana volta à conversa |

Nomes `faq`/`records` são o contrato do kit (`schemas.AgentName`); docs e respostas usam os nomes do enunciado.

## Taxonomia de intenções (o router devolve exatamente uma)

| Intent | Quando | Plano inicial | Se falhar |
|---|---|---|---|
| `knowledge` | como funciona produto, serviço, tarifa ou regra da Getnet, **sem** dado do lojista | `faq` | sem fonte → humano |
| `support` | **dado do próprio lojista**: vendas, recebimentos/depósitos, saldo, antecipação dele, contestação dele | `records` | erro de tool → knowledge → humano |
| `troubleshoot` | **sintoma** na maquininha ou numa transação (não conecta, erro, recusa, travou) | `records` → `faq` (o manual lê o estado do terminal/transação) | humano |
| `general` | fora da Getnet e respondível pela web: clima, câmbio, notícia | `faq` com busca web | **nunca escala** (`NO_ESCALATION`): sem web, diz que não sabe |
| `multi` | duas perguntas de intenções diferentes | união ordenada, em paralelo quando independentes | conforme cada parte |
| `human_request` | pede atendente/humano | `escalation` | — |
| `blocked` | guardrail barrou (injeção, jailbreak, dado de terceiro, abuso) | — | recusa padrão |

**Desempates (testados):**
- Sintoma ("não conecta", "erro", "recusada", "travou") → `troubleshoot`, mesmo com "minha maquininha".
- Dado ("quando cai", "quanto vendi", "meu saldo", "antecipar o meu valor") → `support`.
- "Preciso de / como funciona / posso" sobre produto, **sem pedir dado do lojista** → `knowledge`.
  Ex.: AC-04 ("Do I need a bank account to receive my sales via Pix?") é `knowledge` (a v3 errou isto).
- Produto que a Getnet não tem ou outro banco → `knowledge` (RAG não acha → humano), não `general`.

## Ferramentas do Customer Support Agent (nomes congelados)

| Tool | Tipo | Risco | Retorno |
|---|---|---|---|
| `get_settlements` | leitura | — | agenda de recebimentos: venda, valor, modalidade, data prevista (dias úteis), status, total e disponível p/ antecipar |
| `get_transactions` | leitura | — | transações recentes aprovadas e recusadas, código e motivo da recusa, se é contestável |
| `get_terminal_status` | leitura | — | maquininhas: modelo, conexão (chip/Wi-Fi), sinal, último contato, bobina, bloqueio |
| `restart_terminal` | escrita | normal | `pending_action`; executa após confirmação do dono |
| `request_anticipation` | escrita | high | confirmação do dono **e** aprovação de operador |
| `open_chargeback` | escrita | high | idem; **só transação aprovada** |

Identidade: sempre do servidor (`sub` do JWT; `user_id` do corpo só em `AUTH_MODE=demo`, marcado
`identity_verified=false`). Lojistas do CRM fictício: `cliente1988` (cenários) e `cliente2024`.

## Requisitos

| ID | Requisito | Módulo | Prova |
|---|---|---|---|
| FR-01 | ≥ 3 agentes + comunicação clara (chamada direta; especialistas como tools do supervisor LLM) | `router.py`, `supervisor.py`, `agents.py` | split `replan`, ablação com × sem |
| FR-02 | Router é a entrada e decide agente ou sequência | `domain/challenge/flow.py` | `tests/test_domain_router.py`, `intent_acc` |
| FR-03 | RAG ingestão → armazenamento → busca → geração sobre as URLs indicadas | `ingest.py`, `rag.py`, `data/sources.json` | Recall@3/MRR, ablação de buscadores |
| FR-04 | Busca web para perguntas gerais | `agents.faq_agent` (`general`) | AC-02, AC-07 |
| FR-05 | Support com ≥ 2 tools sobre dados do lojista | `domain/challenge/tools.py` | `tests/test_tools.py`, AC-03/05/08 |
| FR-06 | `POST /chat` `{"message","user_id"}` → JSON útil | `api.py` | `tests/test_domain_http.py`, eval via HTTP no container |
| FR-07 | Bônus: 4º agente humano + handoff sem confiança | `escalation`, `/operator` | split `replan`, HITL |
| FR-08 | Bônus: guardrails (inseguro, irrelevante, sensível, não suportado) | `guardrails.py`, `grounding.py` | split `guardrail` |
| FR-09 | Bônus: avaliação e observabilidade | `evals/`, `/metrics` | gate no CI, ao vivo pass^3, juiz calibrado |
| NFR-01 | Sobe com um comando; 2 réplicas testadas | `Dockerfile`, compose | `check.py --docker --scale` |
| NFR-02 | Funciona sem chave de LLM (offline determinístico), respondendo no idioma da pergunta e em frase | `domain/challenge/` | `language_acc` |
| NFR-03 | `AUTH_MODE=demo` no compose (payload literal); `required` testado | `api.py`, `auth.py` | `tests/test_domain_http.py` |

## Cenários de aceite (literais do enunciado; `user_id` = `cliente1988`)

| ID | Mensagem | Intent | Rota / tool | Handoff |
|---|---|---|---|---|
| AC-01 | What's the difference between the Get Clássica and the Get Smart? | knowledge | faq com fontes | não |
| AC-02 | What's the weather forecast in Porto Alegre tomorrow? | general | faq + web | não |
| AC-03 | When will the money from yesterday's sales be deposited? | support | `get_settlements` | não |
| AC-04 | Do I need a bank account to receive my sales via Pix? | knowledge | faq com fontes | não |
| AC-05 | My card machine won't connect to the internet, what should I do? | troubleshoot | `get_terminal_status` + faq | não |
| AC-06 | How does receivables advance (antecipação) work with Getnet? | knowledge | faq com fontes | não |
| AC-07 | What's the euro exchange rate today? | general | faq + web | não |
| AC-08 | My card machine is showing a transaction decline error. | troubleshoot | `get_transactions` + faq | não |
| AC-09 | How many installments can I split a sale into with the crediário? | knowledge | faq com fontes | não |
| AC-10 | Can I sell through WhatsApp using the Payment Link? | knowledge | faq com fontes | não |

Resposta sempre no idioma da pergunta. Cada AC é `official-NN` em `evals/cases.jsonl`.

## Splits do eval

`official` (10, gate) · `replan` (≥ 20, gate) · `parallel` (gate) · `approval` (gate) · frente Q em
`evals/cases.d/q.jsonl`: `official-var`, `memory` (multi-turno), `guardrail` (gate), `hard` (fora do gate) ·
`heldout` (`evals/heldout_blind.jsonl`, autor cego só com enunciado + esta SPEC, fora do gate, nunca editado,
nenhuma frase dele em regra ou exemplo).

## Fora de escopo

Integração real com sistemas Getnet (CRM fictício em `data/crm/`); pagamento real; réplicas em vários hosts
(ADR 0002: mesmo host testado).

## CRM fictício (ids congelados; a frente T implementa, os casos usam)

| Lojista | Maquininhas | Transações relevantes |
|---|---|---|
| `cliente1988` | `T1988A` Get Smart, Wi-Fi, online · `T1988B` Get Clássica, chip, **sem sinal** | `TX1004` aprovada crédito R$ 350,00 (contestável) · `TX1007` **recusada** código 51 (saldo insuficiente do portador) · `TX1008` recusada código 05 (não autorizada pelo emissor) · vendas de **ontem** em débito e crédito |
| `cliente2024` | `T2024A` Get Smart, online | `TX2001` aprovada · `TX2002` recusada código 51 |

Datas relativas a hoje (ontem, anteontem); liquidação em dias úteis: Pix D0, débito D+1, crédito D+30.
