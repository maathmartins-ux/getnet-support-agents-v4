# Eval ao vivo por split + juiz + README, no PowerShell do Windows (5.1 ou 7).
#
# Uso (de qualquer pasta; roda bem em segundo plano desde T+30):
#   powershell -ExecutionPolicy Bypass -File <projeto>\scripts\live_eval.ps1 -MaxUsd 3
#   powershell -ExecutionPolicy Bypass -File <projeto>\scripts\live_eval.ps1 -Splits official,replan -Workers 4
#   Start-Job { powershell -ExecutionPolicy Bypass -File <projeto>\scripts\live_eval.ps1 -MaxUsd 3 }
#
# - Lê o .env do projeto SEM imprimir valores (a chave nunca aparece no console nem no log).
# - O teto -MaxUsd vale para a rodada inteira: cada split recebe o que sobrou do anterior; o juiz, o resto.
#   Ao estourar, o split para limpo e grava o parcial em evals/results/live_<split>.json.
# - -Repeat 3 por padrão (pass^3): o gasto é ~3x o de uma rodada; -Repeat 1 para um smoke barato.
# - Sem chave: o run_evals sai com código 2 e mensagem clara; o script para sem traceback.
# - Salvo em UTF-8 COM BOM: sem ele o PowerShell 5.1 lê como ANSI e os acentos saem quebrados.
param(
    [string[]]$Splits = @(),
    [double]$MaxUsd = 3.0,
    [int]$Workers = 4,
    [int]$Repeat = 3,  # cada caso roda N vezes: pass^k e instáveis (1 rodada esconde o caso que acerta às vezes; custo x N)
    [switch]$NoJudge,
    [switch]$SemReadme,  # só ao vivo + juiz: o README é da sessão principal (pc_live.ps1 usa isto)
    [switch]$DryRun   # mostra splits e teto e sai, sem gastar
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
# LIVE_EVAL_PY: intérprete explícito (CI Linux não tem .venv; Getnet v3 31/10/2026: CI vermelho do G1 ao fim)
$py = if ($env:LIVE_EVAL_PY) { $env:LIVE_EVAL_PY } else { Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { Write-Host "Falta a venv em $py ou LIVE_EVAL_PY (references/brief-de-fork.md, seção Ambiente)."; exit 1 }

if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=' } | ForEach-Object {
        $name, $value = $_ -split '=', 2
        Set-Item -Path "env:$($name.Trim())" -Value $value.Trim()
    }
}
$env:PYTHONIOENCODING = "utf-8"

# Via `powershell -File`, "-Splits a,b" chega como UM texto "a,b" (smoke de 31/10/2026): separa por vírgula.
$Splits = @($Splits | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($Splits.Count -eq 0) {
    # Todos os splits presentes nos casos, na ordem em que aparecem.
    $Splits = & $py -c "import json,pathlib; s=[]; [s.append(c['split']) for p in ['evals/cases.jsonl', *sorted(map(str, pathlib.Path('evals').glob('cases.d/*.jsonl'))), 'evals/heldout_blind.jsonl'] if pathlib.Path(p).exists() for c in map(json.loads, filter(str.strip, open(p, encoding='utf-8'))) if c['split'] not in s]; print(' '.join(s))"
    $Splits = $Splits -split ' '
}

if ($DryRun) {
    $teto = $MaxUsd.ToString([Globalization.CultureInfo]::InvariantCulture)
    Write-Host "DRYRUN splits=$($Splits -join '|') teto=$teto workers=$Workers repeat=$Repeat juiz=$(-not $NoJudge)"
    exit 0
}

$remaining = $MaxUsd
foreach ($split in $Splits) {
    if ($remaining -le 0) { Write-Host "Teto de US$ $MaxUsd atingido; split $split não rodado."; break }
    $budget = [math]::Round($remaining, 4).ToString([Globalization.CultureInfo]::InvariantCulture)
    Write-Host "== split $split (teto restante US$ $budget)"
    & $py -m evals.run_evals --live --split $split --max-usd $budget --workers $Workers --repeat $Repeat
    if ($LASTEXITCODE -eq 2) { Write-Host "Sem credencial: na nuvem, APP_ANTHROPIC_API_KEY nas variáveis do ambiente; no PC, ANTHROPIC_API_KEY no .env."; exit 2 }
    $out = "evals/results/live_$split.json"
    if (Test-Path $out) {
        $spent = (Get-Content $out -Raw -Encoding UTF8 | ConvertFrom-Json).spent_usd
        $remaining = $remaining - [double]$spent
    }
}

if (-not $NoJudge -and $remaining -gt 0) {
    $env:LLM_PROVIDER = "anthropic"
    $budget = [math]::Round($remaining, 4).ToString([Globalization.CultureInfo]::InvariantCulture)
    & $py -m evals.judge --max-usd $budget
}
if ($SemReadme) { exit 0 }
$env:LLM_PROVIDER = "offline"
& $py -m evals.run_evals | Out-Null   # relatório offline (bloco principal do README e gate)
& $py scripts/fill_readme.py
