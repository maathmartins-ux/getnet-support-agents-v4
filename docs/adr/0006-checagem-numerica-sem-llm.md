# ADR 0006 — Checagem numérica e flags de risco sem LLM, em todo turno

Status: aceito · desafio Getnet v4

## Contexto

A métrica de negócio é resolução automática **sem número inventado**. Num atendimento de adquirência, número
errado é dano real: data de depósito, valor a receber, parcelas do crediário, taxa. Promessa ("garantimos que
cai amanhã", "sem nenhuma tarifa") também. Um juiz LLM em cada turno dobraria custo e latência e seria, ele
mesmo, probabilístico.

## Decisão

Em todo turno, depois da redação, `src/app/pipeline.py` chama duas funções de `src/app/grounding.py`, ambas
sem LLM:

- `unsupported_numbers(answer, evidence, question)`: extrai todo número da resposta (decimal pt/en, milhar,
  datas ISO e dd/mm, escalas "mil"/"million", percentuais) e confere contra a evidência do turno (trechos do
  RAG e retorno das ferramentas), a pergunta e as falas anteriores do lojista e do operador. O que sobra vai
  para `unsupported_numbers` na resposta, para o log (`numeric_ungrounded`, com `trace_id`) e para a métrica
  `numeric_ungrounded_total`.
- `risk_flags(answer, evidence)`: frases de promessa financeira e compromisso legal que aparecem na resposta
  **e não** na evidência viram rótulos em `risk_flags` e em `risk_flags_total{flag}`.

O alerta `NumeroSemBase` (`ops/alerts.yml`) dispara com mais de 5 números sem base por hora.

## Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| Juiz LLM no caminho da resposta | custo e p95 por turno; resultado não determinístico, difícil de testar no CI |
| Bloquear a resposta com número sem base | falso positivo (número reescrito por extenso, arredondado) derrubaria respostas boas; hoje a medida vem antes do bloqueio |
| Só confiar no prompt ("não invente") | sem medida não há como saber se funciona |

## Consequências

- `numbers_acc` do eval e o alerta medem a mesma coisa, offline e em produção.
- Teste: `tests/test_grounding.py::test_pipeline_flags_invented_number_and_counts_it`.
- Limite: a checagem é por valor, não por significado: um número certo no lugar errado passa; o juiz LLM do
  eval (fora do caminho da resposta) cobre parte disso.
