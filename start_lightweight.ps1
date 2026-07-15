# Start only link analysis and the Markdown reader.
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path $PSScriptRoot).Path
$Backend = Join-Path $Root "backend"
$AgentRoot = Join-Path $Root "src\agent"
$LogDir = Join-Path $Backend ".run"
$PidFile = Join-Path $LogDir "uvicorn-lite.pid"
$LogFile = Join-Path $LogDir "uvicorn-lite.log"
$ErrFile = Join-Path $LogDir "uvicorn-lite.err.log"

Write-Host "[1/3] Stopping an existing backend on port $Port ..."
$env:SBA_BACKEND_PORT = "$Port"
& (Join-Path $Root "scripts\backend\stop_backend.ps1")

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$env:PYTHONPATH = "$Backend;$AgentRoot"
$env:SBA_AGENT_CONFIG = Join-Path $AgentRoot "config.json"
$env:SBA_KB_DIR = Join-Path $AgentRoot "knowledge_base"
$env:SBA_LITE_MODE = "1"
$env:SUBSCRIPTION_SCHEDULER_ENABLED = "0"
$env:FAVORITES_SCHEDULER_ENABLED = "0"
$env:RSS_SCHEDULER_ENABLED = "0"
$env:SKILL_AUTO_SYNC_ON_START = "0"
$env:SKILL_INTEL_BACKFILL_ON_START = "0"

$Python = "py"
$PyPrefix = @("-3.12")
$VenvPython = Join-Path $Root "..\.venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    & $VenvPython -c "import uvicorn" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $Python = $VenvPython
        $PyPrefix = @()
    }
}

$UvicornArgs = @(
    "-m", "uvicorn", "app.lite_main:app",
    "--host", $BindHost,
    "--port", "$Port",
    "--log-level", "info"
)

Write-Host "[2/3] Starting lightweight backend (no Redis auto-start) ..."
Write-Host "      Enabled : link analysis, Markdown reader"
Write-Host "      Disabled: AI chat, RAG, RSS, subscriptions, schedulers, automation"
$ProcessArgs = $PyPrefix + $UvicornArgs
$Proc = Start-Process -FilePath $Python `
    -ArgumentList $ProcessArgs `
    -WorkingDirectory $Backend `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrFile `
    -PassThru `
    -WindowStyle Hidden
$Proc.Id | Set-Content -LiteralPath $PidFile -Encoding ascii

Write-Host "[3/3] Waiting for lightweight health check ..."
$Ready = $false
for ($i = 1; $i -le 45; $i++) {
    Start-Sleep -Seconds 1
    try {
        $Response = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/api/lite/status" -TimeoutSec 3
        if ($Response.ok -and $Response.mode -eq "lite") {
            $Ready = $true
            break
        }
    } catch { }
}

if (-not $Ready) {
    Write-Host "[ERROR] Lightweight backend did not become ready." -ForegroundColor Red
    Get-Content -LiteralPath $ErrFile -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "[OK] SuperBizAgent Lite is ready: http://127.0.0.1:${Port}/" -ForegroundColor Green
if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:${Port}/"
}
