# Atalhos para Linux/CI. No Windows, rode os comandos python diretamente (ou scripts/live_eval.ps1).
.PHONY: check check-docker eval live judge readme up scale

check:
	python scripts/check.py

check-docker:
	python scripts/check.py --docker

eval:
	python -m evals.run_evals --compare-retrievers
	python -m evals.run_evals --ablate-replan

live:
	python -m evals.run_evals --live --max-usd $${MAX_USD:-3} --workers 4

judge:
	LLM_PROVIDER=anthropic python -m evals.judge --max-usd $${MAX_USD:-1}

readme: eval
	python scripts/fill_readme.py

up:
	docker compose up -d --build

scale:
	python scripts/scale_smoke.py
