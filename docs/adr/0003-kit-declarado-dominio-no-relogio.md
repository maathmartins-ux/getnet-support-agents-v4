# ADR 0003 — Kit declarado no primeiro commit, domínio Getnet escrito no relógio

Status: aceito · desafio Getnet v4

## Contexto

O desafio é cronometrado e avalia um sistema multiagente completo: API, RAG, ferramentas, HITL, avaliação,
observabilidade e produção. Escrever do zero a infraestrutura genérica (FastAPI, Docker, CI, runner de eval,
JWT, fila humana, métricas) consumiria quase todo o prazo e deixaria pouco tempo para o que diferencia a
solução: entender o negócio da Getnet (lojistas, maquininhas, recebíveis) e acertar os 10 cenários.
Ao mesmo tempo, um avaliador precisa saber, sem confiar na palavra do autor, o que foi feito antes e o que
foi feito durante a prova.

## Decisão

- A infraestrutura genérica vem de um kit próprio, preparado antes do desafio e **declarado no primeiro
  commit** (`chore: scaffold from entregar-desafio-cronometrado kit v3.7.0`).
- O motor (`src/app/*.py`) é **congelado** em `OWNERSHIP.json`; o desafio só muda o domínio
  (`src/app/domain/challenge/`, `src/app/domain/challenge_prompts/`), dados (`data/`), casos (`evals/`) e docs.
- A SPEC (`docs/SPEC.md`) é commitada antes de qualquer código de domínio e também é congelada.
- O trabalho do domínio é dividido em frentes com posse de arquivos (`OWNERSHIP.json[forks]`); a posse é
  verificada antes de cada commit (`scripts/fork_check.py`) e no merge (`scripts/check_ownership.py`).

## Como o `git log` prova

`git log --format='%h %ad %s' --date=iso` mostra, em ordem: o commit do kit (`chore: scaffold`), o pré-voo do
domínio (`preflight:`), a SPEC com os casos (`spec:`) e só depois os commits das frentes. A seção 7 do README
(linha do tempo) e a seção 8 (kit × relógio, em linhas) são geradas do git por `scripts/fill_readme.py`, não
escritas à mão.

## Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| Escrever tudo no relógio | o tempo iria para FastAPI, JWT e CI, e não para taxonomia, RAG e ferramentas da Getnet |
| Usar o kit sem declarar | o avaliador não distinguiria preparo de execução; ruim para a avaliação e desonesto |
| Framework de agentes pronto (LangGraph, CrewAI) | mais dependência e menos controle do contrato `ok/insufficient/error`; o fallback determinístico ficaria difícil de testar |

## Consequências

- O relógio vai para o domínio: SPEC, corpus, glossário, regras do Router, ferramentas, casos e prompts.
- Motor congelado reduz conflito entre frentes paralelas; o custo é que uma melhoria do motor durante a
  prova exige decisão explícita do regente, não edição livre.
- Quem avalia lê a §8 do README e o `git log` e vê a proporção kit × relógio medida, não declarada.
