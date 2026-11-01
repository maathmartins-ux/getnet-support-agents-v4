# ADR 0004 — Supervisor LLM com agentes como tools e tabela determinística de fallback

Status: aceito · desafio Getnet v4

## Contexto

O enunciado pede um Router que "manages the workflow and data flow" entre o Knowledge Agent, o Customer
Support Agent e, como bônus, o Human Escalation Agent. Os cenários exigem sequência (AC-05: estado da
maquininha **e** manual) e recuperação de falha (sem fonte → humano; ferramenta quebrada → base de
conhecimento → humano). O CI roda sem chave de LLM e o provedor pode cair em produção.

## Decisão

- O Router (`src/app/router.py`) só classifica: intenção, idioma, confiança e plano inicial vindo de
  `PLANS` (`src/app/domain/challenge/flow.py`).
- O supervisor (`src/app/supervisor.py`) executa o plano. Com LLM (`run_llm`), os agentes são **tools**
  (`faq_agent`, `records_agent`, `escalation_agent`): o modelo lê o `AgentResult` de cada um
  (`ok`/`insufficient`/`error`, motivo, evidência) e escolhe o próximo passo.
- Toda transição proposta pelo LLM é validada contra `ALLOWED`; o teto é `MAX_STEPS = 4` chamadas por turno;
  `escalation` roda sempre sozinho e por último (`SEQUENTIAL`); intents em `NO_ESCALATION` (`general`) nunca
  abrem caso humano.
- Sem LLM, ou se ele falhar, `run_table` segue a **tabela determinística**: `FALLBACK` (status != ok →
  próximo agente) e confiança do Router abaixo de `LOW_CONFIDENCE = 0.4` → escalonamento
  (`supervisor_fallback_total`).
- Agentes independentes do mesmo lote rodam em paralelo (threads), respeitando `DEPENDS`.

## Ablação com × sem

`SUPERVISOR_REPLAN=off` desliga o replanejamento: o plano inicial roda e termina. O split `replan` de
`evals/cases.jsonl` roda nas duas configurações e a §5 do README (bloco Replanejamento, gerado de
`evals/results/`) mostra a diferença. Testes: `tests/test_supervisor.py::test_llm_reads_insufficient_and_replans_to_escalation`
e `tests/test_supervisor.py::test_llm_disallowed_transition_is_refused`.

## Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| Só tabela (grafo fixo) | não aproveita o motivo da falha (ex.: `insufficient` por falta de fonte × `error` de ferramenta) quando há LLM |
| Só LLM, sem tabela | CI não determinístico, e provedor fora = atendimento fora; transição inválida ficaria sem trava |
| Agentes chamando agentes | fluxo implícito, difícil de auditar no log; o supervisor central registra cada passo com `trace_id` |
| asyncio | os agentes fazem I/O síncrono (SQLite, SDK); threads cortam o p95 sem reescrever a pilha |

## Consequências

- Cada turno registra a rota e os replanos (`replans_total{agent,reason,engine}`), auditáveis por `trace_id`.
- O LLM não consegue sair do envelope (`ALLOWED`, `MAX_STEPS`); o ganho dele aparece só onde há escolha.
- Limite: o envelope é escrito à mão; um caso fora dele termina em humano, não em criatividade.
