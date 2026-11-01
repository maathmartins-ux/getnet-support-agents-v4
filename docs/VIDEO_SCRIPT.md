# Roteiro do vídeo — Getnet Support Agents (~4 min, 10 cenas)

Vídeo legendado (sem narração) gerado deste roteiro; respostas reais do `POST /chat` com LLM ligado. Números entre chaves (`{chave}`) são preenchidos pelo
regente a partir de `evals/results/` (o mesmo que gera a §5 do README); nenhum número é escrito à mão.

| # | Cena | Tempo | Tela | Narração |
|---|---|---|---|---|
| 1 | Problema | 0:00–0:20 | enunciado + `docs/SPEC.md` (tabela de cenários) | "Lojistas da Getnet perguntam de tudo num chat só: diferença entre Get Clássica e Get Smart, quando cai o dinheiro de ontem, por que a maquininha não conecta. A meta: resolver sozinho, sem inventar número, e chamar um humano quando não houver confiança." |
| 2 | Arquitetura | 0:20–0:45 | `docs/ARCHITECTURE.md`, diagrama 1 | "Um único POST /chat. O Router classifica em sete intenções com desempates escritos na spec. O supervisor executa o plano: Knowledge Agent com RAG, Customer Support Agent com ferramentas, Human Escalation Agent. Cada agente devolve ok, insufficient ou error, e o supervisor replaneja." |
| 3 | Demo dos cenários | 0:45–1:25 | terminal: `curl` com o payload literal (AC-01, AC-03, AC-05, AC-07) | "Payload literal do enunciado. AC-01 responde com fontes do site. AC-03 consulta a agenda de recebimentos do cliente1988. AC-05 lê o estado da maquininha e só então o manual. AC-07, câmbio, vai para a web e nunca escala. Os dez cenários passam em {official_pass} de 10 offline e {official_live_pass3} com pass^3 ao vivo." |
| 4 | RAG | 1:25–1:50 | `data/sources.json`, `src/app/rag.py`, tabela de recall | "Duas fontes: getnet.net/en, a do enunciado, e site.getnet.com.br, onde estão crediário e Link de Pagamento. BM25 com glossário português-inglês e rerank do top-20. Recall@3 {recall_at_3}, MRR {mrr}. Sem fonte acima do limiar, não afirma: escala." |
| 5 | Ferramentas com confirmação | 1:50–2:15 | resposta com `pending_action`; `POST /actions/{id}/confirm` | "Leitura é direta; escrita gera uma ação pendente que o próprio lojista confirma, com prazo de validade. A identidade vem do token, nunca do texto. Antecipação e contestação são risco alto: precisam também do operador." |
| 6 | HITL | 2:15–2:40 | `/operator` respondendo um caso; próximo turno do lojista | "Pergunta sem fonte abre um caso na fila. O operador responde no console ou no Telegram, e a resposta entra na conversa na hora: o próximo turno já enxerga. Duas respostas simultâneas: só uma vence." |
| 7 | Avaliação | 2:40–3:05 | README §5 (blocos gerados) | "Gate no CI com {cases_total} casos. Intent {intent_acc}, ferramentas {tools_acc}, números com base {numbers_acc}. Replanejamento com versus sem: {replan_with} contra {replan_without}. Juiz calibrado com kappa {judge_kappa}." |
| 8 | Segurança | 3:05–3:25 | `curl` com injeção; resposta `blocked`; token revogado → 401 | "JWT com expiração e revogação, rate limit que falha fechado, guardrails com desofuscação e PII mascarada na entrada, no log e na saída. Número sem base é checado em todo turno, sem LLM." |
| 9 | Produção e réplicas | 3:25–3:45 | `scripts/check.py --docker --scale` verde; `/metrics`; `ops/alerts.yml` | "Um comando sobe tudo. O check roda lint, testes, eval, imagem não-root e duas réplicas atrás de balanceador: a conversa continua de uma para outra e uma réplica cai sem derrubar o atendimento. Sete alertas com runbook; custo por request de {cost_per_request_usd} dólares." |
| 10 | Limitações | 3:45–4:05 | README §9 | "Limites honestos: CRM fictício, réplicas no mesmo host, juiz calibrado por um rotulador, corpus sem coleta agendada. O kit foi declarado no primeiro commit; o domínio Getnet foi escrito no relógio, e o git log mostra." |

## Chaves a preencher

`{official_pass}`, `{official_live_pass3}`, `{recall_at_3}`, `{mrr}`, `{cases_total}`, `{intent_acc}`,
`{tools_acc}`, `{numbers_acc}`, `{replan_with}`, `{replan_without}`, `{judge_kappa}`, `{cost_per_request_usd}`.

## Gravação

`docs/video/walkthrough.mp4`: slides HTML montados de `evals/results/` commitado e de 5 respostas reais capturadas
pelo `TestClient` com LLM ligado (`docs/video/demo.json`), gravados com Playwright (1280×720) e convertidos com
ffmpeg (H.264). Cada número na tela vem do JSON commitado, não de digitação.
