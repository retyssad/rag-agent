param([switch]$SkipIndex)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not (Test-Path -LiteralPath '.env')) { throw '请先按 README 配置 .env，填写真实模型密钥与 MySQL 地址。' }
foreach ($taskPort in @(8000,5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue) {
        throw "端口 $taskPort 已有服务，请先停止已有服务。"
    }
}
$env:PYTHONPATH = Join-Path $taskRoot 'backend'
$env:PYTHONIOENCODING = 'utf-8'
$env:APP_MODE = 'live'
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
docker compose up -d --wait --wait-timeout 180 mysql redis milvus
if ($LASTEXITCODE -ne 0) { throw '基础服务没有健康启动，请检查 Docker 日志。' }
& '.venv\Scripts\python.exe' -m alembic -c backend\alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败' }
if (-not $SkipIndex) {
    & '.venv\Scripts\python.exe' -m scripts.import_data --reindex
    if ($LASTEXITCODE -ne 0) { throw '知识索引失败；请先导入资料，并检查百炼密钥。' }
}
$taskLogs = Join-Path $taskRoot '.local'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$backendProcess = Start-Process -FilePath $taskPython -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs 'backend.out.log') -RedirectStandardError (Join-Path $taskLogs 'backend.err.log') -PassThru
$taskNode = (Get-Command node).Source
$taskVite = Join-Path $taskRoot 'frontend\node_modules\vite\bin\vite.js'
$frontendProcess = Start-Process -FilePath $taskNode -ArgumentList ('"' + $taskVite + '"'),'--host','127.0.0.1','--port','5173','--strictPort' -WorkingDirectory (Join-Path $taskRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs 'frontend.out.log') -RedirectStandardError (Join-Path $taskLogs 'frontend.err.log') -PassThru
@{backend=$backendProcess.Id;frontend=$frontendProcess.Id} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLogs 'demo-processes.json')
Write-Host '真实模式已启动：http://127.0.0.1:5173。当前教练资料是否为演示样例由导入数据决定。'
