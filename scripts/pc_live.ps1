# Eval ao vivo + juiz NO PC (Windows), com o resto da tentativa na nuvem. Kit v3.7 (31/10/2026).
#
# Por quê: na nuvem, a variável com a chave chega trocada aos programas (medido: 107 caracteres sem "sk-ant-")
# e a API recusa. No PC a chave funciona. A etapa é leve (espera resposta da API; sem Docker, sem testes).
#
# ANTES DO RELÓGIO (uma vez; 2 a 5 min):
#   git clone -b kit-v3.7.0 https://github.com/maathmartins-ux/kit-preflight "$env:USERPROFILE\kit"
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\kit\scripts\pc_live.ps1" -Preparar
#   -> cria o Python do projeto em %USERPROFILE%\kit-venv, pede a chave (só asteriscos), guarda em
#      %USERPROFILE%\.kit-live.env (FORA de qualquer repositório) e testa a chave na API, sem custo.
#
# NO DIA (T+33, quando a sessão principal pedir):
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\kit\scripts\pc_live.ps1" -Repo https://github.com/<dono>/<desafio>
#   -> baixa o main do desafio em %USERPROFILE%\desafio-live (pasta descartável: é sobrescrita), roda o eval ao
#      vivo por split + juiz com teto de US$, e envia SÓ evals/results/live_*.json e judge.json para a branch
#      fork/live. A sessão principal junta (check_ownership.py live) e gera o README. O PC nunca mexe no main.
#
# Salvo em UTF-8 COM BOM (o PowerShell 5.1 lê sem BOM como ANSI e quebra os acentos).
param(
    [string]$Repo = "",
    [string]$Branch = "main",
    [switch]$Preparar,
    [double]$MaxUsd = 3.0,
    [string]$Splits = "official,replan,parallel,memory,approval",
    [int]$Repeat = 3,
    [int]$Workers = 4
)
$ErrorActionPreference = "Stop"
$kit = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $env:USERPROFILE "kit-venv"
$py = Join-Path $venv "Scripts\python.exe"
$keyFile = Join-Path $env:USERPROFILE ".kit-live.env"
$work = Join-Path $env:USERPROFILE "desafio-live"

function Passo([string]$t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Parar([string]$t) { Write-Host ""; Write-Host "PAROU: $t" -ForegroundColor Red; exit 1 }

function Instalar([string]$pasta) {
    Passo "instalando dependências do lockfile de $pasta (só muda se o lock mudou)"
    $lock = Join-Path $pasta "requirements-dev.lock"
    if (-not (Test-Path $lock)) { Parar "não achei $lock" }
    $hash = (Get-FileHash $lock -Algorithm SHA256).Hash.Substring(0, 12)
    $stamp = Join-Path $venv ".lock-$hash"
    if (Test-Path $stamp) { Write-Host "já instalado ($hash)"; return }
    & $py -m pip install -q --disable-pip-version-check -r $lock
    if ($LASTEXITCODE -ne 0) { Parar "pip install falhou (mensagem acima). Mande o erro para a sessão do kit." }
    New-Item -ItemType File -Path $stamp -Force | Out-Null
}

function LerChave {
    if (-not (Test-Path $keyFile)) { return $null }
    foreach ($linha in Get-Content $keyFile) {
        if ($linha -match '^\s*ANTHROPIC_API_KEY\s*=\s*(.+)$') { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $null
}

function TestarChave([string]$k) {
    $tipo = "desconhecido"
    if ($k -match '^sk-ant-([a-z]+[0-9]*)-') { $tipo = $Matches[1] }
    try {
        Invoke-WebRequest -Uri "https://api.anthropic.com/v1/models?limit=1" -UseBasicParsing `
            -Headers @{ "x-api-key" = $k; "anthropic-version" = "2023-06-01" } | Out-Null
        Write-Host "chave OK (tipo $tipo, $($k.Length) caracteres): a API aceitou." -ForegroundColor Green
        return $true
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = $_.Exception.Response.StatusCode.value__ }
        Write-Host "a API recusou a chave (HTTP $code; tipo $tipo, $($k.Length) caracteres)." -ForegroundColor Red
        return $false
    }
}

# 1. Python do projeto (fora de qualquer repositório: serve para o kit e para o desafio)
if (-not (Test-Path $py)) {
    Passo "criando o Python do projeto em $venv (primeira vez)"
    python -m venv $venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) { Parar "não consegui criar o venv com 'python -m venv'" }
}

# 2. chave: arquivo fora dos repositórios; pedida com asteriscos se não existir
$chave = LerChave
if (-not $chave) {
    Passo "chave da API (crie em console.anthropic.com -> API Keys; começa com sk-ant-api)"
    $s = Read-Host "Cole a chave e aperte Enter (aparecem só asteriscos)" -AsSecureString
    $chave = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)).Trim().Trim('"').Trim("'")
    if (-not $chave) { Parar "nenhuma chave colada" }
    Set-Content -Path $keyFile -Value "ANTHROPIC_API_KEY=$chave" -Encoding ASCII
    Write-Host "guardada em $keyFile (fora de qualquer repositório)"
}
Passo "testando a chave na API (sem custo)"
if (-not (TestarChave $chave)) {
    Remove-Item $keyFile -ErrorAction SilentlyContinue
    Parar "chave recusada e apagada de $keyFile. Rode de novo com uma chave nova."
}

if ($Preparar) {
    Instalar $kit
    Passo "teste do Python do projeto"
    & $py -c "import anthropic, fastapi, pydantic; print('ok: anthropic', anthropic.__version__)"
    if ($LASTEXITCODE -ne 0) { Parar "o Python do projeto não importa as bibliotecas" }
    Write-Host ""
    Write-Host "PC PRONTO para o eval ao vivo. No dia: pc_live.ps1 -Repo <url do desafio>" -ForegroundColor Green
    exit 0
}

# 3. DIA: copia descartável do main do desafio
if (-not $Repo) { Parar "informe -Repo https://github.com/<dono>/<desafio> (ou use -Preparar antes do relógio)" }
if (Test-Path (Join-Path $work ".git")) {
    Passo "atualizando $work para origin/$Branch (pasta descartável)"
    git -C $work fetch origin $Branch
    if ($LASTEXITCODE -ne 0) { Parar "git fetch falhou" }
    git -C $work checkout -B $Branch "origin/$Branch"
    git -C $work reset --hard "origin/$Branch"
} else {
    Passo "baixando $Repo em $work"
    git clone --branch $Branch $Repo $work
    if ($LASTEXITCODE -ne 0) { Parar "git clone falhou (o GitHub pediu login? faça login no git deste PC)" }
}
Instalar $work

# 4. eval ao vivo + juiz, com o código e os casos DO DESAFIO, sem gerar README (isso é da sessão principal)
Passo "eval ao vivo ($Splits; repeat $Repeat; teto US$ $MaxUsd) + juiz"
$env:ANTHROPIC_API_KEY = $chave
$env:LIVE_EVAL_PY = $py
$env:PYTHONIOENCODING = "utf-8"
$live = Join-Path $work "scripts\live_eval.ps1"
powershell -ExecutionPolicy Bypass -File $live -Splits $Splits -MaxUsd $MaxUsd -Repeat $Repeat -Workers $Workers -SemReadme
$saida = $LASTEXITCODE
# ablação ao vivo: o split replan SEM replanejamento, 1 rodada (~US$ 0,20): o README mostra com x sem
Passo "ablação ao vivo do replanejamento (split replan, sem desvio, 1 rodada)"
Push-Location $work
& $py -m evals.run_evals --live --ablate-replan --repeat 1 --max-usd 0.5 --workers $Workers
Pop-Location
$env:ANTHROPIC_API_KEY = $null
if ($saida -ne 0) { Write-Host "live_eval.ps1 saiu com $saida; o que foi gravado segue para o envio." -ForegroundColor Yellow }

# 5. envia só os resultados para fork/live
Passo "enviando evals/results/live_*.json e judge.json para a branch fork/live"
Set-Location $work
$arquivos = @(Get-ChildItem "evals\results" -Filter "live_*.json" | ForEach-Object { "evals/results/" + $_.Name })
if (Test-Path "evals\results\judge.json") { $arquivos += "evals/results/judge.json" }
if (Test-Path "evals\results\ablation_replan_live.json") { $arquivos += "evals/results/ablation_replan_live.json" }
if ($arquivos.Count -eq 0) { Parar "nenhum resultado ao vivo foi gravado" }
git checkout -B fork/live
git add -- $arquivos
git -c user.name="Matheus (PC)" -c user.email="288624340+maathmartins-ux@users.noreply.github.com" commit -m "live: eval ao vivo e juiz rodados no PC ($Splits; repeat $Repeat; teto US$ $MaxUsd)"
if ($LASTEXITCODE -ne 0) { Parar "git commit falhou" }
git push -f origin fork/live
if ($LASTEXITCODE -ne 0) { Parar "git push falhou (login do git neste PC?)" }
Write-Host ""
Write-Host "PRONTO: resultados em fork/live. Avise a sessão principal: 'live pronto'." -ForegroundColor Green
