"""Configuração lida do ambiente a cada chamada (testes trocam variáveis sem reimportar)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# USD por 1M de tokens (entrada, saída). Fonte: tabela de preços da Anthropic, consultada em
# 30/10/2026 (cache de 06/10/2026). Confira antes de publicar números de custo; modelo fora da
# tabela é contado com custo 0 e aparece como "unpriced" nas métricas.
PRICES_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Papel → (modelo, effort). Cada um troca por env: ROUTER_MODEL, ROUTER_EFFORT, SUPERVISOR_MODEL...
# Effort é declarado sempre (output_config.effort): os padrões mudam entre modelos.
# - router/guardrail: classificação curta com JSON schema → Haiku, low.
# - supervisor: só escolhe o próximo especialista → Sonnet, low (o p95 ao vivo da v2 veio dele em medium).
# - agent: redige com fonte e usa tools → Sonnet, medium.
# - judge: deve ser pelo menos tão capaz quanto o julgado → Opus, medium.
ROLES = {
    "router": ("claude-haiku-5-5", "low"),
    "guardrail": ("claude-haiku-5-5", "low"),
    "supervisor": ("claude-sonnet-5-5", "low"),
    "agent": ("claude-sonnet-5-5", "medium"),
    "judge": ("claude-opus-5-5", "medium"),
    # reranker: só reordena 20 trechos por relevância → Haiku, low. Medido no corpus real da v3 (884 trechos,
    # 45 perguntas): o BM25 acha a fonte no top-20 em 90,4% e no top-3 em 70,8%; o gargalo é a ordem.
    "reranker": ("claude-haiku-5-5", "low"),
}


def model_for(role: str) -> str:
    return os.getenv(f"{role.upper()}_MODEL") or ROLES[role][0]


def effort_for(role: str) -> str:
    return os.getenv(f"{role.upper()}_EFFORT") or ROLES[role][1]


def data_dir() -> Path:
    # Caminho explícito: no container o pacote não mora ao lado de data/, e um índice vazio
    # passa despercebido até o smoke test (lição do desafio de 30/10/2026).
    return Path(os.getenv("DATA_DIR", ROOT / "data"))


def db_path() -> Path:
    return Path(os.getenv("DB_PATH", ROOT / "var" / "app.db"))


def llm_provider() -> str:
    # "offline" é determinístico e roda no CI sem chave; o modo com LLM nunca é pré-requisito.
    return os.getenv("LLM_PROVIDER", "offline")


def auth_mode() -> str:
    # required: identidade só do Bearer token assinado (padrão seguro).
    # demo: aceita user_id do corpo (payload literal de enunciado) e marca identity_verified=false.
    # Ver docs/adr/0001-auth-mode-demo.md.
    mode = os.getenv("AUTH_MODE", "required").lower()
    return mode if mode in ("demo", "required") else "required"


def auth_secret() -> str:
    return os.getenv("AUTH_SECRET", "dev-secret-change-me")


def operator_token() -> str:
    return os.getenv("OPERATOR_TOKEN", "dev-operator-change-me")


def operator_keys() -> dict[str, str]:
    # "ana:<sha256>,bruno:<sha256>" — só o hash fica na configuração (python -m app.auth operator-key ana).
    pairs = (p.split(":", 1) for p in os.getenv("OPERATOR_KEYS", "").split(",") if ":" in p)
    return {name.strip(): digest.strip() for name, digest in pairs if name.strip() and digest.strip()}


def token_ttl_min() -> int:
    # 60 min: sessão de atendimento típica; o cliente renova. Revogação antecipada via POST /auth/revoke.
    return int(os.getenv("TOKEN_TTL_MIN", "60"))


def auth_issuer() -> str:
    return os.getenv("AUTH_ISSUER", "app")


def auth_audience() -> str:
    return os.getenv("AUTH_AUDIENCE", "app-api")


def action_ttl_min() -> int:
    # Ação de escrita pendente expira: confirmar uma proposta velha executaria algo fora de contexto.
    return int(os.getenv("ACTION_TTL_MIN", "15"))


def sla_minutes(priority: str) -> int:
    # Alvo de resposta humana por prioridade; caso vencido é re-escalonado uma vez (store.escalate_overdue).
    return int(os.getenv("SLA_HIGH_MIN", "120") if priority == "high" else os.getenv("SLA_NORMAL_MIN", "480"))


def rate_limit_per_min() -> int:
    # 30/min por chave: folgado para conversa humana, corta script em loop.
    return int(os.getenv("RATE_LIMIT_PER_MIN", "30"))


def retriever_name() -> str:
    # bm25 (padrão, sem dependência) | hybrid (BM25 + embeddings com RRF; extra [hybrid]).
    name = os.getenv("RETRIEVER", "bm25").lower()
    return name if name in ("bm25", "hybrid") else "bm25"


def rerank_enabled() -> bool:
    # RERANK=llm|off|auto (padrão auto: liga só com LLM real; offline e CI ficam determinísticos).
    mode = os.getenv("RERANK", "auto").lower()
    return mode == "llm" or (mode == "auto" and llm_provider() != "offline")


def embed_model() -> str:
    # Multilíngue (pt/en) e pequeno; suportado pelo fastembed. Troque por env se o corpus pedir.
    return os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


def supervisor_replan() -> bool:
    # Ablação (eval --ablate-replan): "off" = só o plano inicial do router, sem desvio. Mede quanto o replanejamento
    # vale (nelsondell, 9,5 na Régua V4: 32/60 com replanejamento × 19/60 sem). Produção: sempre "on".
    return os.getenv("SUPERVISOR_REPLAN", "on").lower() != "off"


def supervisor_parallel() -> bool:
    # Agentes independentes do mesmo passo rodam em threads; "false" serializa (depuração).
    return os.getenv("SUPERVISOR_PARALLEL", "true").lower() != "false"


def coverage_min_score(default: float) -> float:
    # Limiar do portão "sem fonte, sem afirmação". Escolha por medida: run_evals --calibrate-gate.
    value = os.getenv("COVERAGE_MIN_SCORE")
    return float(value) if value else default


def otel_exporter() -> str | None:
    # console | otlp (extra [otel]). Ausente = só log JSON + /metrics.
    value = os.getenv("OTEL_EXPORTER", "").lower()
    return value if value in ("console", "otlp") else None


# --- credencial e endereço do LLM do APP (não do Claude Code) ---------------------------------------------------
# Kit v3.7 (31/10/2026), sessão da nuvem: o Claude Code reserva ANTHROPIC_API_KEY para si (a variável criada no
# ambiente não chegava aos programas) e exporta ANTHROPIC_BASE_URL apontando para a conexão interna dele, que o SDK
# leria sozinho. O app usa APP_ANTHROPIC_API_KEY (ou ANTHROPIC_API_KEY fora da nuvem) e, na nuvem, a API pública.
PUBLIC_API = "https://api.anthropic.com"


def llm_api_key() -> str | None:
    # espaço, aspas ou quebra de linha coladas junto com a chave na tela de variáveis viram 401 na API
    raw = os.getenv("APP_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or ""
    return raw.strip().strip("\"'").strip() or None


def llm_base_url() -> str:
    explicit = os.getenv("APP_ANTHROPIC_BASE_URL")
    if explicit:
        return explicit
    if os.getenv("CLAUDE_CODE_REMOTE") == "true":  # o ANTHROPIC_BASE_URL da sessão é do Claude Code, não do app
        return PUBLIC_API
    return os.getenv("ANTHROPIC_BASE_URL") or PUBLIC_API

