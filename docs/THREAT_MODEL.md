# Modelo de ameaças

Sistema: `POST /chat` (lojista Getnet) → guardrails → router → supervisor → agentes (Knowledge com RAG/web,
Customer Support com tools do CRM, Human Escalation) → resposta; `/mcp` para clientes MCP; `/operator` e
`/actions/*` para operador. Sem números aqui: taxas de bloqueio e falso positivo estão em `evals/results/`
(split `guardrail`).

## Ativos e fronteiras de confiança

| Ativo | Por que importa |
|---|---|
| Dados do lojista no CRM (vendas, recebimentos, transações, maquininhas) | sigilo comercial; um lojista nunca vê o de outro (`cliente1988` × `cliente2024`) |
| Ações de escrita (`restart_terminal`, `request_anticipation`, `open_chargeback`) | efeito financeiro real |
| PII digitada pelo cliente (CPF, cartão, e-mail, telefone) | LGPD |
| Prompt do sistema e instruções dos agentes | contorno de política |
| Base de conhecimento (páginas do site da Getnet) e resultados web | entrada **não confiável** |

Fronteiras: (1) internet → API; (2) texto do usuário → LLM; (3) documento/página web → LLM; (4) LLM → tools;
(5) operador → ação de risco alto.

## STRIDE

| # | STRIDE | Ameaça | Controle | Arquivo | Teste |
|---|---|---|---|---|---|
| S1 | Spoofing | trocar `user_id` no corpo para se passar por outro lojista | identidade só do `sub` do JWT; corpo só em `AUTH_MODE=demo`, marcado `identity_verified=false`; token válido vence o corpo | `src/app/api.py::_identity` | `tests/test_domain_http.py::test_valid_token_wins_over_body_user_id`, `test_official_scenario_over_http_in_demo_mode` |
| S2 | Spoofing | token forjado: sem `exp`, expirado, `alg: none`, assinatura trocada | HS256 obrigatório, `exp`/`iat` com folga, `iss`/`aud`, compare em tempo constante | `src/app/auth.py::decode_token` | `tests/test_domain_http.py::test_bad_tokens_are_401`, `test_bad_token_is_401_even_in_demo_mode` |
| S3 | Spoofing | token vazado continua valendo | revogação por `jti` (`POST /auth/revoke`) | `src/app/auth.py::revoke_token` | `tests/test_domain_http.py::test_revoked_token_is_401` |
| S4 | Spoofing | cliente MCP anônimo | `/mcp` exige JWT antes do protocolo | `src/app/mcp_server.py` | `tests/test_domain_guardrails.py::test_t10_mcp_without_token_is_401` |
| T1 | Tampering | modelo injeta `user_id` de outro lojista na chamada de tool | `run_tool` descarta `user_id` dos args e injeta o do servidor | `src/app/actions.py::run_tool` | `tests/test_domain_guardrails.py::test_t6_tool_ignores_user_id_from_the_model` |
| T2 | Tampering | documento envenenado na base (injeção indireta) | documento com instrução é descartado na carga; trechos entram como `<untrusted_content>` | `src/app/guardrails.py::looks_like_injection`, `src/app/rag.py` | `tests/test_domain_guardrails.py::test_t2_indirect_injection_in_document_is_detected_and_dropped` |
| R1 | Repudiation | "não fui eu que pedi a antecipação" | ação pendente com dono, conversa, args e trilha (`approved_by`); `trace_id` em toda resposta | `src/app/actions.py`, `src/app/store.py` | `tests/test_domain_guardrails.py::test_t8_high_risk_write_needs_operator_after_owner` |
| I1 | Information disclosure | pedir dado de outro lojista ("vendas do outro cliente, cliente2024") | guardrail `other_user_data`; tool filtra por dono; conversa de outro dono = 404/403 | `src/app/guardrails.py`, `src/app/pipeline.py`, `src/app/api.py` | `tests/test_domain_guardrails.py::test_t4_request_for_other_merchant_data_is_blocked`, `test_t4_conversation_of_another_merchant_is_404` |
| I2 | Information disclosure | PII do cliente vai para LLM, log ou resposta | máscara (CPF, cartão com Luhn, e-mail, telefone) antes de qualquer agente; repetida na saída; contato público citado preservado | `src/app/guardrails.py::mask_pii`, `mask_output` | `tests/test_domain_guardrails.py::test_t5_pii_masked_on_input_before_agents`, `test_t5_pii_masked_on_output_but_public_contact_survives`, `test_t5_pii_never_reaches_the_log`; `tests/test_domain_http.py::test_pii_from_the_request_does_not_come_back` |
| I3 | Information disclosure | extrair o prompt do sistema | guardrail `prompt_injection` (`system prompt`, `prompt do sistema`) | `src/app/guardrails.py` | `tests/test_domain_guardrails.py::test_t1_direct_injection_is_blocked_before_any_agent` |
| D1 | Denial of service | rajada de mensagens / custo de LLM | rate limit por usuário verificado e por IP no demo (trocar `user_id` não zera); 429 com `Retry-After` | `src/app/auth.py::LIMITER`, `src/app/api.py::chat` | `tests/test_domain_http.py::test_rate_limit_returns_429_with_retry_after`, `test_demo_rate_limit_is_by_ip_so_switching_user_id_does_not_reset_it` |
| D2 | Denial of service | mensagem gigante | `message` ≤ 4000 caracteres → 422 | `src/app/schemas.py::ChatRequest` | `tests/test_domain_http.py::test_message_over_4000_chars_is_422` |
| E1 | Elevation of privilege | escrita sem consentimento do dono | toda escrita vira `pending_action`; executa só com confirmação do dono, uma vez | `src/app/actions.py::confirm_action` | `tests/test_domain_guardrails.py::test_t7_write_only_runs_after_owner_confirmation` |
| E2 | Elevation of privilege | antecipação/contestação (risco alto) autoaprovada | quatro olhos: dono **e** operador; operador inválido = 403 | `src/app/actions.py`, `src/app/api.py::approve` | `tests/test_domain_guardrails.py::test_t8_high_risk_write_needs_operator_after_owner` |
| E3 | Elevation of privilege | confirmar ação antiga fora de contexto | TTL da ação pendente (`ACTION_TTL_MIN`) → `expired` / 410 | `src/app/actions.py::_expired` | `tests/test_domain_guardrails.py::test_t9_pending_action_expires` |

## OWASP Top 10 para aplicações LLM (2025)

| OWASP 2025 | Ameaça aqui | Controle | Arquivo | Teste / eval |
|---|---|---|---|---|
| LLM01 Prompt Injection (direta) | "ignore as instruções", "você agora é", jailbreak, "ative o modo desenvolvedor" como **ordem** | camada 1 regex pt/en (antes de tudo) + camada 2 classificador LLM sobre texto mascarado; "evite o modo desenvolvedor" (dica do site) não bloqueia | `src/app/guardrails.py` | `test_t1_direct_injection_is_blocked_before_any_agent`, `test_t2_security_tip_about_developer_mode_is_not_injection`; split `guardrail` |
| LLM01 Prompt Injection (ofuscada) | letras espaçadas, l33t, full-width, zero-width | `deobfuscate` (NFKC, zero-width, separador repetido, l33t) só na cópia checada | `src/app/guardrails.py::deobfuscate` | `test_t3_obfuscated_injection_is_blocked`, `test_t3_deobfuscation_keeps_benign_messages` |
| LLM01 Prompt Injection (indireta) | instrução dentro de página, PDF ou texto colado | descarte na carga; passagens como `<untrusted_content>`; texto colado com ordem é bloqueado | `src/app/guardrails.py`, `src/app/rag.py`, `src/app/agents.py` | `test_t2_indirect_injection_in_document_is_detected_and_dropped`; casos `q-gr-29..31` |
| LLM02 Sensitive Information Disclosure | dado de outro lojista; PII em log/LLM/resposta | `other_user_data`; dono na tool; máscara de PII entrada/saída/log | `src/app/guardrails.py`, `src/app/actions.py` | `test_t4_*`, `test_t5_*`, `test_pii_from_the_request_does_not_come_back` |
| LLM03 Supply Chain | dependência ou modelo trocado | lockfile (`requirements-dev.lock`), versão de modelo fixada por papel em config | `requirements-dev.lock`, `src/app/config.py` | CI instala do lock |
| LLM04 Data and Model Poisoning | base de conhecimento envenenada | só hosts permitidos em `data/sources.json`; descarte de documento com instrução | `src/app/ingest.py`, `src/app/rag.py` | `test_t2_indirect_injection_in_document_is_detected_and_dropped` |
| LLM05 Improper Output Handling | saída do LLM usada como comando ou HTML | resposta é texto em JSON tipado (pydantic); tools só com args do schema; console do operador escapa conteúdo (`esc`) | `src/app/schemas.py`, `src/app/actions.py`, `src/app/static/operator.html` | contrato JSON em `tests/test_domain_http.py` |
| LLM06 Excessive Agency | modelo executa escrita ou age por outro lojista | `user_id` do servidor; escrita = pendente; risco alto = operador; TTL | `src/app/actions.py` | `test_t6_*`, `test_t7_*`, `test_t8_*`, `test_t9_*`; split `approval` |
| LLM07 System Prompt Leakage | pedir/imprimir o prompt do sistema | guardrail; prompts sem segredo (segredos só em env) | `src/app/guardrails.py`, `src/app/prompts/` | `test_t1_*`; casos `q-gr-01`, `q-gr-32` |
| LLM08 Vector and Embedding Weaknesses | trecho irrelevante/adversarial no topo da busca | limiar de cobertura (`MIN_SCORE`): sem fonte suficiente → humano, não chute | `src/app/agents.py`, `src/app/config.py` | split `replan`; Recall@3/MRR |
| LLM09 Misinformation | número inventado; "garantimos que cai hoje" | checagem numérica contra evidência; `risk_flags` (promessa financeira, compromisso legal) sem base | `src/app/grounding.py` | `test_t11_financial_promise_without_evidence_is_flagged`; juiz calibrado (TPR/TNR) |
| LLM10 Unbounded Consumption | rajada, mensagem gigante, laço de replanejamento | rate limit, 4000 caracteres, `MAX_STEPS` no supervisor, teto em US$ no eval ao vivo | `src/app/auth.py`, `src/app/schemas.py`, `src/app/supervisor.py` | `test_rate_limit_returns_429_with_retry_after`, `test_message_over_4000_chars_is_422` |

## Falso positivo também é falha

Bloquear o benigno derruba a resolução automática (métrica de negócio). O split `guardrail` traz casos
`blocked: false` parecidos com ataque ("ignorar uma venda × cancelar", "outra pessoa da loja usa a maquininha",
"evite o modo desenvolvedor", mensagem com CPF que deve ser atendida com o CPF mascarado), e
`test_t12_benign_messages_are_not_blocked` trava o mesmo no pytest.

## Riscos residuais (aceitos e monitorados)

- Paráfrase de injeção em idioma/forma que a regex não cobre depende da camada LLM; se ela cair, o atendimento
  segue só com a camada 1 (`guardrail_llm_unavailable_total` alerta).
- "outro lojista" escrito sem as palavras-gatilho passa da camada 1; a proteção real é a tool filtrar por dono
  (T1/I1), não o texto.
- `AUTH_MODE=demo` existe para o avaliador rodar o payload literal; a identidade não é verificada e a resposta
  diz isso (`identity_verified=false`). Produção usa `required` (ADR 0001).
