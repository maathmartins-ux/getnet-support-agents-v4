#!/usr/bin/env bash
# Eval ao vivo por split + juiz + README (Linux/macOS/Git Bash). Mesmo comportamento de live_eval.ps1.
#
# Uso:
#   scripts/live_eval.sh --max-usd 3
#   scripts/live_eval.sh --splits official,replan --workers 4 --repeat 1   # padrão 3 (pass^3); 1 = smoke
#   scripts/live_eval.sh --dry-run            # mostra splits e teto, sem gastar
#
# - Lê o .env do projeto SEM imprimir valores (a chave nunca aparece no console nem no log).
# - O teto vale para a rodada inteira: cada split recebe o que sobrou do anterior; o juiz, o resto.
# - Sem chave: run_evals sai com código 2 e mensagem clara; o script para sem traceback.
set -euo pipefail

SPLITS="" MAX_USD="3" WORKERS="4" REPEAT="3" NO_JUDGE=0 DRY=0  # repeat 3: pass^3 (custo ~3x; --repeat 1 = smoke)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --splits) SPLITS="$2"; shift 2 ;;
    --max-usd) MAX_USD="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --repeat) REPEAT="$2"; shift 2 ;;
    --no-judge) NO_JUDGE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) echo "opção desconhecida: $1" >&2; exit 64 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# LIVE_EVAL_PY: intérprete explícito (CI Linux não tem .venv; Getnet v3 31/10/2026: CI vermelho do G1 ao fim)
PY="${LIVE_EVAL_PY:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$ROOT/.venv/Scripts/python.exe"  # Git Bash no Windows
[[ -x "$PY" ]] || { echo "Falta a venv em $ROOT/.venv ou LIVE_EVAL_PY (references/brief-de-fork.md, seção Ambiente)."; exit 1; }

if [[ -f .env ]]; then  # exporta KEY=VALUE sem ecoar nada
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && export "$key=${value%$'\r'}"
  done < <(grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' .env | sed -E 's/^[[:space:]]+//')
fi
export PYTHONIOENCODING=utf-8

if [[ -z "$SPLITS" ]]; then  # todos os splits presentes nos casos, na ordem em que aparecem
  SPLITS="$("$PY" -c "import json,pathlib; s=[]; [s.append(c['split']) for p in ['evals/cases.jsonl', *sorted(map(str, pathlib.Path('evals').glob('cases.d/*.jsonl'))), 'evals/heldout_blind.jsonl'] if pathlib.Path(p).exists() for c in map(json.loads, filter(str.strip, open(p, encoding='utf-8'))) if c['split'] not in s]; print(','.join(s))")"
fi
IFS=',' read -r -a LIST <<< "$SPLITS"
CLEAN=()
for s in "${LIST[@]}"; do s="${s// /}"; [[ -n "$s" ]] && CLEAN+=("$s"); done

if [[ $DRY -eq 1 ]]; then
  JUDGE=$([[ $NO_JUDGE -eq 1 ]] && echo False || echo True)
  echo "DRYRUN splits=$(IFS='|'; echo "${CLEAN[*]}") teto=$MAX_USD workers=$WORKERS repeat=$REPEAT juiz=$JUDGE"
  exit 0
fi

remaining="$MAX_USD"
for split in "${CLEAN[@]}"; do
  if "$PY" -c "import sys; sys.exit(0 if float('$remaining') > 0 else 1)"; then :; else
    echo "Teto de US\$ $MAX_USD atingido; split $split não rodado."; break
  fi
  echo "== split $split (teto restante US\$ $remaining)"
  set +e
  "$PY" -m evals.run_evals --live --split "$split" --max-usd "$remaining" --workers "$WORKERS" --repeat "$REPEAT"
  code=$?
  set -e
  if [[ $code -eq 2 ]]; then echo "Sem credencial: na nuvem, APP_ANTHROPIC_API_KEY nas variáveis do ambiente; no PC, ANTHROPIC_API_KEY no .env."; exit 2; fi
  out="evals/results/live_$split.json"
  if [[ -f "$out" ]]; then
    remaining="$("$PY" -c "import json; print(round(float('$remaining') - json.load(open('$out', encoding='utf-8'))['spent_usd'], 4))")"
  fi
done

if [[ $NO_JUDGE -eq 0 ]] && "$PY" -c "import sys; sys.exit(0 if float('$remaining') > 0 else 1)"; then
  LLM_PROVIDER=anthropic "$PY" -m evals.judge --max-usd "$remaining"
fi
LLM_PROVIDER=offline "$PY" -m evals.run_evals > /dev/null  # relatório offline (bloco principal do README e gate)
"$PY" scripts/fill_readme.py
