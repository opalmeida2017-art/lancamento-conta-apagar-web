# Inicia o servidor web autonomo
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Criando ambiente virtual..."
    python -m venv venv
}

Write-Host "Verificando dependencias..."
.\venv\Scripts\pip install -r requirements.txt -q

if (-not (Test-Path ".\.env")) {
    if (Test-Path ".\.env.example") {
        Copy-Item ".\.env.example" ".\.env"
        Write-Host "Arquivo .env criado - ajuste PG_USER e PG_PASSWORD." -ForegroundColor Yellow
    }
}

if (-not (Test-Path ".\.env")) {
    $hostPort = "8090"
} else {
    $hostPort = (Select-String -Path ".\.env" -Pattern "^NFE_WEB_PORT=" | ForEach-Object { $_.Line -replace "NFE_WEB_PORT=", "" }).Trim()
    if (-not $hostPort) { $hostPort = "8090" }
}
$webHost = "0.0.0.0"
if (Test-Path ".\.env") {
    $h = (Select-String -Path ".\.env" -Pattern "^NFE_WEB_HOST=" | ForEach-Object { $_.Line -replace "NFE_WEB_HOST=", "" }).Trim()
    if ($h) { $webHost = $h }
}

$env:PYTHONPATH = $PSScriptRoot
Write-Host ""
Write-Host "=== Sistema Web Autonomo NFe ===" -ForegroundColor Cyan
Write-Host "Banco: PostgreSQL (ver .env: PG_HOST, PG_PORT, PG_DATABASE)"
Write-Host "URL:   http://localhost:$hostPort"
Write-Host "(porta 8090 — sem conflito com BIWEB :5000)"
Write-Host ""
.\venv\Scripts\python.exe -m uvicorn backend.app.main:app --host $webHost --port $hostPort --reload
