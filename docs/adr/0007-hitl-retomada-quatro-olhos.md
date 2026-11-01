# ADR 0007 — HITL com retomada imediata e quatro olhos em ação de risco

Status: aceito · desafio Getnet v4

## Contexto

O bônus do enunciado é um Human Escalation Agent quando não há confiança. Em suporte a lojista, o humano
não pode ser um beco sem saída ("seu ticket foi aberto"): a resposta precisa voltar para a conversa. E duas
ferramentas do Customer Support Agent mexem em dinheiro (`request_anticipation`, `open_chargeback`): o
lojista confirmar não basta, porque um token roubado ou um prompt manipulado confirmaria também.

## Decisão

- **Fila persistida:** o agente `escalation` grava o caso no SQLite (`src/app/store.py`) com motivo
  (`handoff_reason`, ex.: `faq_insufficient`, `low_confidence`), prioridade e SLA; um caso por conversa,
  mesmo com duas réplicas (`tests/test_runtime_store.py::test_two_replicas_escalating_the_same_conversation_open_one_ticket`).
- **Operador:** console `/operator`, `GET /handoffs`, `POST /handoffs/{id}/reply` com `X-Operator-Token`
  (chave pessoal por operador, registrada na auditoria); Telegram opcional (`src/app/notify.py`).
- **Retomada imediata:** `resume_after_human` (`src/app/resume.py`) põe a resposta do operador na conversa do
  lojista na hora; com LLM redige no idioma do lojista, sem LLM entrega o texto literal identificado
  ("Resposta do atendente" / "Agent's reply"). O próximo turno já enxerga a resposta.
- **Quatro olhos** (`src/app/actions.py`): escrita gera `pending_action` com TTL (`ACTION_TTL_MIN`); o dono
  confirma; se a ferramenta é `risk: high`, a ação vai para `awaiting_operator` e só executa com aprovação do
  operador (`approvals_requested_total`, `approvals_decided_total`). A troca de estado é atômica: executa uma
  vez só.
- **SLA:** caso vencido é re-escalonado uma única vez (`sla_escalations_total`, alerta `SLAVencido`).

## Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| Operador só fecha o ticket | o lojista nunca vê a resposta no chat; a conversa morre |
| Confirmação só do dono para toda escrita | antecipação e contestação mexem em dinheiro; um fator só é pouco |
| Aprovação humana para toda escrita | `restart_terminal` é reversível e frequente; travaria o atendimento à toa |
| Fila externa (Zendesk, Jira) | integração fora do escopo; a interface `Store` isola a troca |

## Consequências

- Provas: `tests/test_hitl.py::test_operator_reply_resumes_the_conversation_immediately`,
  `tests/test_hitl.py::test_high_risk_action_needs_owner_then_operator_and_runs_once`,
  `tests/test_hitl.py::test_two_simultaneous_replies_only_one_wins`, `tests/test_hitl.py::test_pending_action_expires`.
- Limite: um operador por caso, sem roteamento por especialidade; sem limite de valor configurável para o
  `high` (toda antecipação vai ao operador).
