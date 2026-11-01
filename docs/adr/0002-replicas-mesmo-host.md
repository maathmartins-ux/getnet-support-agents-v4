# ADR 0002 — Duas réplicas no mesmo host com SQLite compartilhado; vários hosts com Postgres

Status: aceito no kit v3.7 (01/11/2026)

**Contexto.** A Régua V4 pontua estado e produção com réplicas: as soluções públicas de 9+ mostram duas cópias da
API atrás de um balanceador. O kit v3.6 declarava "réplicas fora do escopo". Montando o teste de réplicas apareceu
um defeito real: "existe caso aberto? senão cria" em dois passos deixava duas réplicas abrirem dois casos para a
mesma conversa (`tests/test_runtime_store.py::test_two_replicas_escalating_the_same_conversation_open_one_ticket`
falhava 5 de 5 vezes antes da correção).

**Decisão.** Estado num único SQLite em WAL num volume compartilhado; as garantias de "uma vez só" ficam no banco
(índice único parcial para caso aberto por conversa; claim atômico de ação e de resposta do operador). O teste
`scripts/scale_smoke.py` (`make scale`, e no CI via `check.py --docker --scale`) sobe 2 réplicas atrás de um
balanceador e prova: a conversa continua entre réplicas, escalonamento simultâneo abre um caso, uma réplica cai e
o atendimento segue.

**Limite e próximo passo.** Vale para réplicas no MESMO host (o SQLite não é banco de rede). Para vários hosts:
`Store` em Postgres (mesmas tabelas; o índice único parcial e os claims viram as mesmas cláusulas SQL), rate
limit em Redis.

**Alternativas descartadas.** Postgres desde o início: dependência e tempo de subida no relógio para um ganho que o
teste no mesmo host já demonstra. Lock em memória do processo: não vale entre réplicas.
