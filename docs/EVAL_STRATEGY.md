# Estratégia de avaliação

Escrita antes dos números. Nenhum número aparece aqui: todo valor citado no README sai de `evals/results/`
(gerado por `python -m evals.run_evals` e preenchido por `scripts/fill_readme.py`). Se um número não está num
arquivo de `evals/results/`, ele não existe.

## 1. O que medimos (taxonomia de checagens)

Cada caso de `evals/cases.jsonl` + `evals/cases.d/*.jsonl` declara só o que importa para ele em `expect`; o
runner (`evals/run_evals.py::score`) confere cada campo de forma determinística, sem LLM.

| Checagem | Campo de `expect` | Pergunta que responde | Tipo |
|---|---|---|---|
| Intenção | `intent` | o router mandou para o especialista certo? | código, exata |
| Idioma | `language` | a resposta saiu no idioma da pergunta (detectado no TEXTO, não no rótulo)? | código |
| Ferramentas | `tools` | o Customer Support Agent chamou as tools certas (subconjunto)? | código |
| Escalonamento | `handoff` | foi para humano quando devia, e só então? | código |
| Desfecho por agente | `agents` / `forbidden_agents` | quem de fato atuou (handoff por desfecho, não por rótulo) | código |
| Bloqueio | `blocked` | o guardrail barrou o ataque **e** deixou passar o benigno? | código |
| Ação pendente | `pending_action` | escrita virou ação pendente em vez de executar? | código |
| Replanejamento | `replanned` | o supervisor mudou o plano quando um agente devolveu `insufficient`/`error`? | código |
| Vazamento | `not_contains` | PII ou dado de terceiro não aparece na resposta | código |
| Números | (sempre) | todo número da resposta tem base em fonte, tool, template ou pergunta (`grounding.py`) | código |
| Recuperação | `doc_ids` | Recall@3 e MRR do RAG | código |
| Fundamentação | (juiz) | toda afirmação está na evidência do turno? | LLM calibrado |

Checagem por código primeiro; o juiz LLM só onde não há regra (fundamentação de texto livre).

## 2. Splits e por que cada um existe

| Split | Arquivo | O que prova | Gate do CI |
|---|---|---|---|
| `official` | `evals/cases.jsonl` | os 10 cenários literais do enunciado (AC-01..AC-10) | sim |
| `replan` | `evals/cases.jsonl` | supervisor replaneja e escala sem fonte | sim |
| `parallel` | `evals/cases.jsonl` | `multi` executa partes independentes em paralelo | sim |
| `approval` | `evals/cases.jsonl` | escrita com confirmação; risco alto com operador | sim |
| `official-var` | `evals/cases.d/q.jsonl` | os 10 cenários com **outras palavras**, pt e en: o router generaliza, não decora | sim |
| `memory` | `evals/cases.d/q.jsonl` | seguimento multi-turno (`before`) herda o assunto ("e a de ontem?", "and the other machine?") | sim |
| `guardrail` | `evals/cases.d/q.jsonl` | ataques bloqueados (injeção direta/indireta, ofuscada, jailbreak como ordem, dado de outro lojista, abuso) **e** benignos parecidos que não podem ser bloqueados | sim |
| `hard` | `evals/cases.d/q.jsonl` | paráfrase difícil, erro de digitação, mistura de idiomas, gíria | **não** |
| `heldout` | `evals/heldout_blind.jsonl` | autor cego (só enunciado + SPEC), nunca editado | **não** |

Regra anti-contaminação: nenhuma frase de `official-var`, `memory`, `guardrail`, `hard` nem dos exemplos de
intenção (`data/intent_examples.d/q.jsonl`) repete 5 palavras seguidas dos cenários oficiais; exemplos de
intenção não repetem casos. Assim, acerto no eval não é cópia do few-shot.

**Por que `hard` e `heldout` ficam fora do gate.** O gate decide merge. Se um split entra no gate, quem mexe no
sistema passa a ajustar regra até ele ficar verde, e o split deixa de medir generalização (lei de Goodhart).
`hard` é onde esperamos errar: serve para ver a tendência entre versões, não para travar o CI. `heldout` é o
único conjunto que ninguém viu ao ajustar; gateá-lo convidaria a lê-lo. Os dois são **medidos e publicados**
com IC, só não bloqueiam.

## 3. Gate do CI

- `python -m evals.run_evals` (offline, determinístico, `LLM_PROVIDER=offline`, um SQLite por caso) roda dentro de
  `scripts/check.py` no CI (`.github/workflows/ci.yml`).
- Reprova (código 1) se a acurácia de qualquer split gateado ficar abaixo de `EVAL_GATE` (padrão 0,9) ou se
  houver crash. O limiar é por desafio e nunca é baixado "para passar".
- Cada frente roda o eval dos seus splits em `var/` antes de commitar (`scripts/fork_check.py`), nunca em
  `evals/results/`.

## 4. Ao vivo: pass^k

Offline prova o contrato; ao vivo prova o modelo. `python -m evals.run_evals --live --split <s> --max-usd <teto>`
roda cada caso **3 vezes** (`LIVE_REPEAT`) e publica:

- **pass@1**: média por execução;
- **pass^3**: fração de casos que passam nas 3 execuções. Um agente de atendimento precisa acertar sempre, não
  às vezes; a diferença entre pass@1 e pass^3 é a instabilidade;
- lista de casos **instáveis** (passam em algumas execuções): primeira fila de investigação.

O teto em US$ é projetado antes de cada chamada; ao estourar, para limpo e grava o parcial (código 3).

## 5. Incerteza: IC de Wilson

Toda taxa sai com `k`, `n` e intervalo de Wilson a 95% (`run_evals.wilson`). Com n pequeno o intervalo é largo, e
é isso que o leitor precisa ver: duas versões cujos ICs se sobrepõem não são "melhor" e "pior". Wilson e não o
intervalo normal porque com taxas perto de 0 ou 1 o normal sai de [0, 1] e fica estreito demais.

## 6. Juiz LLM calibrado (TPR/TNR)

- Rótulos humanos em `evals/judge_calibration.jsonl` + `evals/judge_calibration.d/*.jsonl`: pergunta, resposta,
  fontes (`source_ids`/`sources`), dados de ferramenta (`tool_calls`, `evidence`) e `human_grounded`.
- A calibração da frente Q cobre as três origens de evidência: **RAG** (produto Getnet), **tool** (CRM com os ids
  congelados da SPEC: `TX1004`, `TX1007`, `TX1008`, `T1988A`, `T1988B`) e **web** (clima, câmbio). Metade
  positivos. Negativos de propósito difíceis: número trocado, promessa sem base, dado de outra transação ou de
  outro lojista, afirmação além da fonte.
- Publicamos **TPR** (acerto nos grounded) e **TNR** (acerto nos não grounded) com IC, além de concordância e
  kappa de Cohen. Concordância alta sozinha esconde um juiz que aprova tudo; TNR baixo denuncia.
- O juiz recebe a mesma evidência que a resposta usou (trechos, resultado de tool, título/URL web): dado de
  ferramenta é fonte.

## 7. Ablação do replanejamento

`python -m evals.run_evals --ablate-replan` mede o split `replan` **sem** replanejamento (plano fixo do router) e
grava `ablation_replan*.json`. A diferença com × sem é a evidência de que o supervisor agrega valor (FR-01), não
uma afirmação de arquitetura.

## 8. Matriz de confusão e de falhas

Todo relatório traz `failure_matrix`: confusão de intenção (esperada × obtida, `crash` incluído) e contagem de
falhas por checagem e por split. É por ela que se decide o próximo ajuste: um par dominante (ex.: `troubleshoot`
→ `support`) aponta a regra de desempate da SPEC que falhou.

## 9. Monitoramento em produção

O eval offline não vê o tráfego real. Em produção, os mesmos sinais viram métricas e logs (`/metrics`,
`app/observability.py`, `/analytics`):

| Sinal | Fonte | Alerta quando |
|---|---|---|
| bloqueios do guardrail por motivo (camada regex × LLM) | `guardrails` + spans | salto de bloqueio (ataque) ou queda a zero (regra quebrada) |
| `guardrail_llm_unavailable_total` | classificador | camada 2 fora do ar |
| `numeric_ungrounded_total`, `risk_flagged` | `grounding.py` | número sem base ou promessa financeira na resposta |
| taxa de handoff e SLA vencido | `escalation`, `/handoffs` | fila crescendo |
| 👍/👎 por `trace_id` | `POST /feedback` | queda de satisfação por intenção |
| `auth_failures_total`, `rate_limited_total` | `api.py` | força bruta ou abuso |
| custo e latência por agente | `usage`, spans | regressão de custo/p95 |

Respostas com 👎, handoff ou `risk_flags` são amostradas, rotuladas por humano e entram como novos casos
(`hard` primeiro; viram gate só depois de estáveis) e como rótulos do juiz. O ciclo fecha: produção alimenta o
eval, o eval protege a produção.
