$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskFile = Join-Path $taskRoot '.local\demo-processes.json'
if (-not (Test-Path -LiteralPath $taskFile)) { Write-Host '没有记录到由 start-demo.ps1 启动的服务'; exit 0 }
$taskProcesses = Get-Content -Raw -LiteralPath $taskFile | ConvertFrom-Json
foreach ($taskPid in @($taskProcesses.backend,$taskProcesses.frontend)) {
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskPid" -ErrorAction SilentlyContinue
    # PID 可复用：只停止命令行仍指向本项目的 uvicorn/vite 进程。
    if ($taskProcess -and $taskProcess.CommandLine -and (($taskProcess.ExecutablePath -eq (Join-Path $taskRoot '.venv\Scripts\python.exe') -and $taskProcess.CommandLine -match 'uvicorn app.main:app') -or ($taskProcess.CommandLine -like "*$taskRoot*vite*"))) {
        Stop-Process -Id $taskPid -ErrorAction SilentlyContinue
    }
}
Write-Host '已停止记录中的演示服务。Docker 服务未停止。'
