import os
from pathlib import Path

import pytest

# Domínio do kit ANTES de importar qualquer módulo do app: os testes do kit nunca enxergam o domínio do desafio
# (app/domain/challenge.py). Getnet v3: o domínio trocou o código e 59 testes do kit quebraram (CI vermelho).
os.environ["DOMAIN"] = "example"

from app import notify
from app.auth import LIMITER
from app.config import ROOT
from app.observability import configure_otel
from evals import judge, run_evals

# Dados do kit: os testes do kit nunca leem o que o domínio troca no relógio (data/, retrieval, calibração).
# Getnet v3 (31/10/2026): trocar só os dados quebrava 24 de 187 testes e atrasou o G1 em 17 min (regra D2).
KIT_DATA = Path(__file__).parent / "fixtures" / "kit_data"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    # Cada teste com banco próprio, modo offline, sem rede e segredos conhecidos: nada vaza entre testes.
    monkeypatch.setenv("DOMAIN", "example")
    monkeypatch.setenv("DATA_DIR", str(KIT_DATA / "data"))
    monkeypatch.setattr(run_evals, "CASES", KIT_DATA / "cases.jsonl")
    monkeypatch.setattr(run_evals, "HELDOUT", KIT_DATA / "heldout_blind.jsonl")  # o kit não tem held-out
    monkeypatch.setattr(run_evals, "RETRIEVAL", KIT_DATA / "retrieval.jsonl")
    # Nenhum teste grava em evals/results/: lá mora a evidência commitada que o README cita (Getnet v3: rodar o
    # pytest sobrescrevia latest.json com números da loja de exemplo).
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(run_evals, "RESULTS", tmp_path / "results" / "latest.json")
    monkeypatch.setattr(judge, "CALIBRATION", KIT_DATA / "judge_calibration.jsonl")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_SECRET", "test-secret")
    monkeypatch.setenv("OPERATOR_TOKEN", "test-operator")
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1000")
    monkeypatch.setenv("RETRIEVER", "bm25")
    monkeypatch.setenv("SLA_CHECK_SECONDS", "0")  # sem laço de SLA em segundo plano nos servidores de teste
    # chaves e chaves de comportamento do ambiente real (na nuvem, APP_ANTHROPIC_API_KEY vem do ambiente) nunca
    # entram nos testes: offline é offline, e a ablação do replanejamento não vaza para outros testes
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET", "OTEL_EXPORTER",
                "SUPERVISOR_PARALLEL", "SUPERVISOR_REPLAN", "APP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
                "APP_ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(notify, "_transport", None)
    LIMITER.reset()
    yield
    LIMITER.reset()
    configure_otel(None)  # desliga o exportador se algum teste ligou


@pytest.fixture
def domain_data(monkeypatch):
    """Para testes do DOMÍNIO (escritos no relógio): domínio `challenge`, data/ e evals/ reais.

    Sem `app/domain/challenge.py` (kit antes do pré-voo), o teste de domínio é pulado, nunca roda no exemplo.
    """
    import importlib.util

    if importlib.util.find_spec("app.domain.challenge") is None:
        pytest.skip("sem app/domain/challenge.py: rode scripts/preflight.py")
    monkeypatch.setenv("DOMAIN", "challenge")
    monkeypatch.setenv("DATA_DIR", str(ROOT / "data"))
    monkeypatch.setattr(run_evals, "CASES", ROOT / "evals" / "cases.jsonl")
    monkeypatch.setattr(run_evals, "HELDOUT", ROOT / "evals" / "heldout_blind.jsonl")
    monkeypatch.setattr(run_evals, "RETRIEVAL", ROOT / "evals" / "retrieval.jsonl")
    monkeypatch.setattr(judge, "CALIBRATION", ROOT / "evals" / "judge_calibration.jsonl")
