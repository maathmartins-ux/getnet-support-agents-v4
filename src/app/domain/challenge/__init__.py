"""Domínio do desafio (sessão principal): junta flow + tools e guarda o limiar calibrado.

MIN_SCORE sai de `python -m evals.run_evals --calibrate-gate` NO CORPUS DO DESAFIO; troque aqui,
nunca no motor nem no domínio de exemplo (v3: trocar o padrão do kit quebrou 14 testes).
"""
from __future__ import annotations

from pathlib import Path

from .flow import *  # noqa: F403
from .tools import *  # noqa: F403

# --- portão "sem fonte, sem afirmação" ----------------------------------------------------------------------
# BM25 bruto abaixo disso = sem fonte. O valor do desafio sai de `run_evals --calibrate-gate` e vai para o
# challenge.py (ou COVERAGE_MIN_SCORE no compose). Getnet v3: trocar o padrão do KIT quebrou 14 testes.
MIN_SCORE = 0.5

# --- prompts ------------------------------------------------------------------------------------------------
# None = prompts do kit (src/app/prompts). O pré-voo aponta o challenge para src/app/domain/challenge_prompts.
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "challenge_prompts"

