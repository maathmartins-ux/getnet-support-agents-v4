# ADR 0005 — RAG com duas fontes, BM25 + glossário pt↔en + rerank; híbrido atrás de flag

Status: aceito · desafio Getnet v4

## Contexto

O enunciado indica `www.getnet.net/en` como base do Knowledge Agent. Vários cenários citam produtos do mercado
brasileiro (Get Clássica × Get Smart em AC-01, crediário em AC-09, Link de Pagamento em AC-10), que estão em
`site.getnet.com.br`, em português. As perguntas chegam em inglês ou português, então pergunta e documento
podem estar em línguas diferentes ("receivables advance" × "antecipação de recebíveis"). Responder sem fonte
num serviço financeiro é pior do que escalar.

## Decisão

- **Duas fontes** em `data/sources.json`: `www.getnet.net/en` (indicada no enunciado) e `site.getnet.com.br`;
  `python -m app.ingest` (`src/app/ingest.py`) baixa só hosts de `allowed_hosts`, limpa o HTML e grava trechos
  com título, URL e data de coleta.
- **BM25** (`src/app/rag.py`) como buscador padrão, com título em peso 2 e **glossário pt↔en**
  (`data/glossary.json`) expandindo a consulta antes da busca.
- **Rerank** do top-20 (`RERANK_POOL`) pelo Haiku quando há LLM (`RERANK=auto`); se o rerank falhar, fica a
  ordem do BM25 (`rerank_fallback_total`).
- **Híbrido** (BM25 + embeddings multilíngues por RRF, `RETRIEVER=hybrid`) atrás de flag e de extra opcional
  (`.[hybrid]`), fora da imagem padrão.
- **Portão sem fonte:** abaixo de `COVERAGE_MIN_SCORE` o Knowledge Agent devolve `insufficient` e o supervisor
  escala; o limiar é escolhido por medida (`tests/test_rag.py::test_gate_threshold_is_chosen_by_measurement`).
- Documento com instrução embutida é descartado na carga (`poisoned_docs_dropped_total`).

## Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| Só a URL do enunciado | AC-01, AC-09 e AC-10 ficariam sem fonte e iriam para humano |
| Embeddings desde o início | dependência pesada (modelo, imagem maior) sem ganho medido; entra só se Recall@3/MRR mostrarem |
| Banco vetorial externo | mais um serviço para subir; o corpus cabe em memória |
| Tradução da pergunta por LLM | custo e latência por turno; o glossário cobre os termos do domínio de forma determinística |

## Consequências

- Recall@3 e MRR por buscador (BM25, híbrido, rerank) saem de `evals/retrieval.jsonl` e vão à §5 do README.
- O corpus é um recorte coletado numa data; mudança no site exige rodar a ingestão de novo.
- O glossário é mantido à mão: termo novo sem entrada pode não casar entre línguas.
