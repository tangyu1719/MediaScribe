[CmdletBinding()]
param(
    [switch]$SkipSystemPackages,
    [switch]$SkipPythonDependencies,
    [switch]$SkipRagModel,
    [switch]$NoStart,
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-WingetPackage([string]$Command, [string]$Id, [string]$Label) {
    if (Test-Command $Command) {
        Write-Host "[OK] $Label"
        return
    }
    if (-not (Test-Command "winget")) {
        throw "缺少 $Label，且未找到 winget。请先安装 Microsoft App Installer 后重试。"
    }
    Write-Host "[安装] $Label ($Id)"
    & winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "$Label 安装失败，winget exit=$LASTEXITCODE"
    }
}

function Invoke-SystemPython([string[]]$PythonArgs) {
    if (Test-Command "py") {
        & py -3.11 @PythonArgs
    } elseif (Test-Command "python") {
        & python @PythonArgs
    } else {
        throw "Python 已安装但当前终端 PATH 尚未刷新。请重开 PowerShell 后再次运行。"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python 命令失败，exit=$LASTEXITCODE"
    }
}

function New-RandomSecret([int]$Bytes = 24) {
    $buffer = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($buffer)).Replace("-", "").ToLowerInvariant()
}

function Read-Plain([string]$Prompt, [string]$Default = "") {
    if ($NonInteractive) { return $Default }
    $suffix = if ($Default) { " [$Default]" } else { "" }
    $value = Read-Host "$Prompt$suffix"
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    if ($value -match "[\r\n]") { throw "$Prompt 不能包含换行" }
    return $value.Trim()
}

function Read-Secret([string]$Prompt, [string]$Default = "") {
    if ($NonInteractive) { return $Default }
    $secure = Read-Host $Prompt -AsSecureString
    $value = [Net.NetworkCredential]::new("", $secure).Password
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    if ($value -match "[\r\n]") { throw "$Prompt 不能包含换行" }
    return $value.Trim()
}

if (-not $SkipSystemPackages) {
    Install-WingetPackage "git" "Git.Git" "Git"
    Install-WingetPackage "python" "Python.Python.3.11" "Python 3.11"
    Install-WingetPackage "node" "OpenJS.NodeJS.LTS" "Node.js LTS"
    Install-WingetPackage "ffmpeg" "Gyan.FFmpeg" "FFmpeg"
    Install-WingetPackage "docker" "Docker.DockerDesktop" "Docker Desktop"
}

Write-Host "[检查] 基础环境"
Invoke-SystemPython @("--version")
if (-not (Test-Command "git")) { throw "未找到 Git，请重开终端后重试。" }
if (-not (Test-Command "ffmpeg")) { throw "未找到 FFmpeg，请重开终端后重试。" }
if (-not (Test-Command "docker")) { throw "未找到 Docker，请启动 Docker Desktop 或重开终端后重试。" }
& docker compose version
if ($LASTEXITCODE -ne 0) { throw "Docker Compose v2 不可用。" }
& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker daemon 未启动。请启动 Docker Desktop 后重试。" }

if (-not $SkipPythonDependencies) {
    if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
        Write-Host "[安装] 创建 Python 虚拟环境"
        Invoke-SystemPython @("-m", "venv", ".venv")
    }
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r backend/requirements-deploy.txt
    & $VenvPython -m yt_dlp --version
    if ((Test-Command "npm") -and (Test-Path -LiteralPath "package.json")) {
        & npm install
        & npx playwright install chromium
    }
}

$existing = @{}
if (Test-Path -LiteralPath ".env") {
    Get-Content -LiteralPath ".env" -Encoding UTF8 | ForEach-Object {
        if ($_ -match "^([A-Z0-9_]+)=(.*)$") { $existing[$Matches[1]] = $Matches[2] }
    }
}

function Existing-OrEnv([string]$Name) {
    $fromProcess = [Environment]::GetEnvironmentVariable($Name)
    if ($fromProcess) { return $fromProcess }
    if ($existing.ContainsKey($Name)) { return [string]$existing[$Name] }
    return ""
}

Write-Host "[配置] 私密值只写入本机 .env；config.yaml 始终保留环境变量占位符。"
$volcKey = Read-Secret "火山方舟 API Key（可留空，LLM 将不可用）" (Existing-OrEnv "VOLC_API_KEY")
$modelQa = Read-Plain "问答模型 endpoint id" (Existing-OrEnv "LLM_MODEL_QA")
$modelReason = Read-Plain "摘要/推理模型 endpoint id" (Existing-OrEnv "LLM_MODEL_REASON")
$feishuId = Read-Plain "飞书 App ID（可留空）" (Existing-OrEnv "FEISHU_APP_ID")
$feishuSecret = Read-Secret "飞书 App Secret（可留空）" (Existing-OrEnv "FEISHU_APP_SECRET")
$xhsGaia = Read-Plain "Chrome 预期用户名称（可留空）" (Existing-OrEnv "SBA_CHROME_EXPECTED_GAIA")
$xhsEmail = Read-Plain "Chrome 预期邮箱（可留空）" (Existing-OrEnv "SBA_CHROME_EXPECTED_EMAIL")
$xhsNickname = Read-Plain "小红书本人昵称（可留空）" (Existing-OrEnv "SBA_XHS_OWNER_NICKNAME")
$xhsRedId = Read-Plain "小红书号（可留空）" (Existing-OrEnv "XHS_FAVORITES_RED_ID")
$xhsCreatorId = Read-Plain "小红书 creator id（可留空）" (Existing-OrEnv "XHS_FAVORITES_CREATOR_ID")

$mysqlRoot = Existing-OrEnv "MYSQL_ROOT_PASSWORD"; if (-not $mysqlRoot) { $mysqlRoot = New-RandomSecret }
$mysqlPass = Existing-OrEnv "MYSQL_PASSWORD"; if (-not $mysqlPass) { $mysqlPass = New-RandomSecret }
$redisPass = Existing-OrEnv "REDIS_PASSWORD"; if (-not $redisPass) { $redisPass = New-RandomSecret }
$minioPass = Existing-OrEnv "MINIO_ROOT_PASSWORD"; if (-not $minioPass) { $minioPass = New-RandomSecret }
$jwtSecret = Existing-OrEnv "SBA_JWT_SECRET"; if (-not $jwtSecret) { $jwtSecret = New-RandomSecret 32 }

$lines = @(
    "VOLC_API_KEY=$volcKey"
    "VOLC_BASE_URL=https://ark.cn-beijing.volces.com/api/v3"
    "LLM_MODEL_QA=$modelQa"
    "LLM_MODEL_REASON=$modelReason"
    "FEISHU_APP_ID=$feishuId"
    "FEISHU_APP_SECRET=$feishuSecret"
    "MYSQL_ROOT_PASSWORD=$mysqlRoot"
    "MYSQL_DATABASE=superbizagent"
    "MYSQL_USER=mediascribe"
    "MYSQL_PASSWORD=$mysqlPass"
    "REDIS_PASSWORD=$redisPass"
    "MINIO_ROOT_USER=mediascribe"
    "MINIO_ROOT_PASSWORD=$minioPass"
    "SBA_JWT_SECRET=$jwtSecret"
    "KB_BACKEND=milvus"
    "MILVUS_HOST=milvus"
    "MILVUS_PORT=19530"
    "WEB_PORT=8000"
    "SBA_CHROME_EXPECTED_GAIA=$xhsGaia"
    "SBA_CHROME_EXPECTED_EMAIL=$xhsEmail"
    "SBA_XHS_OWNER_NICKNAME=$xhsNickname"
    "XHS_FAVORITES_RED_ID=$xhsRedId"
    "XHS_FAVORITES_CREATOR_ID=$xhsCreatorId"
)
[IO.File]::WriteAllLines((Join-Path $RepoRoot ".env"), $lines, [Text.UTF8Encoding]::new($false))
Write-Host "[OK] 已生成 .env（已被 gitignore）"

& docker compose --env-file .env -f deploy/docker-compose.yml config --quiet
if ($LASTEXITCODE -ne 0) { throw "Docker Compose 配置校验失败。" }

if (-not $SkipRagModel) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) {
        if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }
        & $VenvPython -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5', cache_folder='src/agent/knowledge_base/models')"
    }
}

if (-not $NoStart) {
    Write-Host "[启动] MySQL、Redis、Milvus 与 MediaScribe"
    & docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
    if ($LASTEXITCODE -ne 0) { throw "Docker 服务启动失败。" }
    & docker compose --env-file .env -f deploy/docker-compose.yml ps
    Write-Host "完成：http://127.0.0.1:8000/"
}
