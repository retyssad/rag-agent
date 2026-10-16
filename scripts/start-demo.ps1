param([switch]$Install)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
foreach ($taskPort in @(8000,5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue) {
        throw "端口 $taskPort 已有服务，请先停止已有服务，避免同时启动两份。"
    }
}
$env:PYTHONPATH = Join-Path $taskRoot 'backend'
$env:PYTHONIOENCODING = 'utf-8'
$env:APP_MODE = 'demo'
$env:DATABASE_URL = 'sqlite:///./.local/coach.db'
if ($Install -or -not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    & '.venv\Scripts\python.exe' -m pip install -r backend\requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
}
if ($Install -or -not (Test-Path -LiteralPath 'frontend\node_modules')) {
    Push-Location frontend
    try { npm ci; if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' } }
    finally { Pop-Location }
}
& '.venv\Scripts\python.exe' -m alembic -c backend\alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败' }
& '.venv\Scripts\python.exe' -m scripts.import_data data/demo/dataset.json
if ($LASTEXITCODE -ne 0) { throw '演示数据导入失败' }
# 服务在隐藏的窗口运行，前端固定端口避免悄悄跳到另一端口。
$taskLogs = Join-Path $taskRoot '.local'
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$backendProcess = Start-Process -FilePath $taskPython -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs 'backend.out.log') -RedirectStandardError (Join-Path $taskLogs 'backend.err.log') -PassThru
$taskNode = (Get-Command node).Source
$taskVite = Join-Path $taskRoot 'frontend\node_modules\vite\bin\vite.js'
$frontendProcess = Start-Process -FilePath $taskNode -ArgumentList ('"' + $taskVite + '"'),'--host','127.0.0.1','--port','5173','--strictPort' -WorkingDirectory (Join-Path $taskRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs 'frontend.out.log') -RedirectStandardError (Join-Path $taskLogs 'frontend.err.log') -PassThru
@{backend=$backendProcess.Id;frontend=$frontendProcess.Id} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLogs 'demo-processes.json')
Write-Host '浏览器打开 http://127.0.0.1:5173；后端文档 http://127.0.0.1:8000/docs。日志位于 .local。'
