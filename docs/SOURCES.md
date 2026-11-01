# Fontes do corpus (frente K)

Corpus em `data/knowledge/<grupo>/<slug>.jsonl`, gerado por `python -m app.ingest` (trechos literais de 300–800
caracteres, sha256 por trecho). `allowed_hosts`: `www.getnet.net` (fonte indicada no enunciado, `/en`) e
`site.getnet.com.br` (site Brasil, onde estão os produtos dos cenários: Get Clássica/Smart, Pix, antecipação,
Crediário, Link de Pagamento, Get Conta, Get Tap). Ids: `<prefixo>-<slug>-NN` (prefixos `en`, `prod`, `sup`).

| grupo | URL | ids | trechos | coleta (UTC) |
|---|---|---|---|---|
| en | https://www.getnet.net/en | `en-home-01..07` | 7 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/our-solutions/in-person-payments | `en-in-person-payments-01..07` | 7 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/our-solutions/online-payments | `en-online-payments-01..06` | 6 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/our-solutions/omnichannel | `en-omnichannel-01..05` | 5 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/our-solutions/value-added-solutions | `en-value-added-solutions-01..05` | 5 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/our-solutions/agentic-commerce | `en-agentic-commerce-01..12` | 12 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/your-business/large-enterprises | `en-large-enterprises-01..05` | 5 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/your-business/industries/restaurants | `en-restaurants-01..11` | 11 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/your-business/industries/travel | `en-travel-01..09` | 9 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/your-business/industries/health-and-beauty | `en-health-and-beauty-01..10` | 10 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/corporate/about-us | `en-about-us-01..05` | 5 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/developers | `en-developers-01..05` | 5 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/partners | `en-partners-01..07` | 7 | 2026-11-01T20:00:50+00:00 |
| en | https://www.getnet.net/en/contact | `en-contact-01..01` | 1 | 2026-11-01T20:00:50+00:00 |
| produtos | https://site.getnet.com.br/ | `prod-home-01..11` | 11 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/todas-as-maquininhas/ | `prod-todas-as-maquininhas-01..06` | 6 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/maquininha-de-cartao/ | `prod-maquininha-de-cartao-01..06` | 6 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/pix/ | `prod-pix-01..03` | 3 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/pix-na-maquininha/ | `prod-pix-na-maquininha-01..05` | 5 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/antecipacao/ | `prod-antecipacao-01..04` | 4 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/crediario/ | `prod-crediario-01..11` | 11 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/link-de-pagamento/ | `prod-link-de-pagamento-01..05` | 5 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/vender-pelo-whatsapp/ | `prod-vender-pelo-whatsapp-01..13` | 13 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/taxas/ | `prod-taxas-01..01` | 1 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/conta-digital/ | `prod-conta-digital-01..02` | 2 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/get-tap/ | `prod-get-tap-01..07` | 7 | 2026-11-01T20:01:00+00:00 |
| produtos | https://site.getnet.com.br/chargeback/ | `prod-chargeback-01..06` | 6 | 2026-11-01T20:01:00+00:00 |
| suporte | https://site.getnet.com.br/duvidas/ | `sup-duvidas-01..110` | 110 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/operacional/ | `sup-operacional-01..09` | 9 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/aluguel/ | `sup-aluguel-01..07` | 7 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/aluguel-zero/ | `sup-aluguel-zero-01..01` | 1 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/get-smart/ | `sup-get-smart-01..16` | 16 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/get-tap/ | `sup-get-tap-01..15` | 15 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/pix/ | `sup-pix-01..03` | 3 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/pix-mais-respostas/ | `sup-pix-mais-respostas-01..26` | 26 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/parcelado/ | `sup-parcelado-01..02` | 2 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/antecipacao-de-vendas/ | `sup-antecipacao-de-vendas-01..14` | 14 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/crediario/ | `sup-crediario-01..02` | 2 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/link-de-pagamento/ | `sup-link-de-pagamento-01..09` | 9 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/taxas/ | `sup-taxas-01..09` | 9 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/get-conta/ | `sup-get-conta-01..12` | 12 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/cancelamento-descredenciamento/ | `sup-cancelamento-01..02` | 2 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/app-getnet/ | `sup-app-getnet-01..04` | 4 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/bandeiras/ | `sup-bandeiras-01..03` | 3 | 2026-11-01T20:01:10+00:00 |
| suporte | https://site.getnet.com.br/duvidas/pre-autorizacao/ | `sup-pre-autorizacao-01..01` | 1 | 2026-11-01T20:01:10+00:00 |

**Total: 420 trechos** (nenhum descartado como `poisoned_doc_dropped` no carregamento).

## Lacunas conhecidas

- **Idioma:** o site Brasil só existe em português; `www.getnet.net/en` é institucional (soluções, setores) e não
  cobre Pix, Crediário, antecipação nem Get Conta. Pergunta em inglês sobre esses produtos depende do glossário
  pt→en (`data/glossary.json`, aplicado por `agents.expand_query` antes da busca). O `evals.run_evals`
  mede o BM25 com a pergunta crua (sem glossário), por isso a metade em inglês das perguntas pareadas pontua
  bem abaixo da metade em português.
- **Get Clássica:** não há página de FAQ própria; aparece só em tabelas de oferta (`prod-todas-as-maquininhas-05`)
  e depoimentos. A comparação Clássica × Smart se apoia no FAQ da Get Smart (`sup-get-smart-08`, `-10`).
- **Pix sem conta bancária:** nenhuma página diz literalmente "precisa (ou não) de conta bancária"; o mais
  próximo é a elegibilidade do Pix (domicílio bancário + chave, `sup-pix-09`) e o recebimento na Get Conta
  (`sup-get-conta-05`).
- **Problemas de conexão/recusa na maquininha:** o site não tem passo a passo de Wi-Fi/chip nem códigos de
  recusa; esses cenários são das tools (`get_terminal_status`, `get_transactions`), não do corpus.
- **Taxas:** `/taxas/` é um simulador em JavaScript (1 trecho útil); as taxas literais vêm do FAQ (`sup-taxas-*`).
  `/taxas-personalizadas/` não gerou trecho (página só com formulário) e saiu de `sources.json`.
- `/duvidas/chargeback/` dá 404; chargeback vem de `/chargeback/` (blog) e `/duvidas/operacional/`.
- `/duvidas/` (índice) repete o primeiro bloco de cada subpágina, então alguns trechos aparecem duplicados
  com outro id (ex.: `sup-duvidas-64` = `sup-operacional-01`); os rótulos de retrieval listam os dois quando
  ambos respondem.
- Conteúdo de oferta (Black Friday, preços promocionais) é datado da coleta e muda sem aviso.
