param([switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$logDirectory = Join-Path $projectRoot '.logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$frontendDirectory = Join-Path $projectRoot 'frontend'
$backendDirectory = Join-Path $projectRoot 'backend'
$agentDirectory = Join-Path $projectRoot 'agent-service'
$agentPython = Join-Path $agentDirectory '.venv\Scripts\python.exe'
$processFile = Join-Path $logDirectory 'dev-processes.json'
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 5173,8080,8000 -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $alreadyReady = $false
    if (Test-Path -LiteralPath $processFile) {
        try {
            $record = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
            $owned = $true
            foreach ($service in @($record.backend, $record.frontend, $record.agent)) {
                if (-not $service) { $owned = $false; break }
                $existingProcess = Get-Process -Id $service.id -ErrorAction SilentlyContinue
                if (-not $existingProcess -or $existingProcess.StartTime.ToUniversalTime().Ticks -ne ([datetime]$service.started).ToUniversalTime().Ticks) { $owned = $false; break }
            }
            if ($owned) {
                $apiHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/health' -TimeoutSec 2
                $agentHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
                $webHealth = Invoke-WebRequest -Uri 'http://127.0.0.1:5173' -UseBasicParsing -TimeoutSec 2
                $alreadyReady = $apiHealth.status -eq 'UP' -and $agentHealth.status -eq 'UP' -and $webHealth.StatusCode -eq 200
            }
        } catch { $alreadyReady = $false }
    }
    if ($alreadyReady) {
        Write-Host '系统已经运行：http://127.0.0.1:5173'
        if ($OpenBrowser) { Start-Process 'http://127.0.0.1:5173' }
        return
    }
    throw '5173、8080 或 8000 端口已被占用。若本项目尚未就绪，请先双击“一键停止”，再启动。'
}
$nodeExecutable = (Get-Command node -ErrorAction Stop).Source
$javaExecutable = (Get-Command java -ErrorAction Stop).Source
if ($env:JAVA_HOME -and (Test-Path -LiteralPath (Join-Path $env:JAVA_HOME 'bin\java.exe'))) {
    $javaExecutable = Join-Path $env:JAVA_HOME 'bin\java.exe'
}
$mavenExecutable = (Get-Command mvn.cmd -ErrorAction Stop).Source
$npmExecutable = (Get-Command npm.cmd -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $agentPython)) { & (Join-Path $projectRoot 'setup-agent.ps1') }
$configPath = Join-Path $agentDirectory '.env'
if (-not (Test-Path -LiteralPath $configPath)) { Copy-Item -LiteralPath (Join-Path $agentDirectory '.env.example') -Destination $configPath }
$internalToken = $env:ASSESS_SERVICE_TOKEN
if (-not $internalToken) {
    $tokenLine = Get-Content -LiteralPath $configPath | Where-Object { $_ -match '^\s*ASSESS_SERVICE_TOKEN\s*=' } | Select-Object -First 1
    if ($tokenLine) { $internalToken = ($tokenLine -replace '^\s*ASSESS_SERVICE_TOKEN\s*=\s*', '').Trim().Trim('"').Trim("'") }
}
if ($internalToken) { $env:AGENT_SERVICE_TOKEN = $internalToken }
$env:ASSESS_UPLOAD_ROOT = Join-Path $backendDirectory 'data\uploads'
Push-Location $frontendDirectory
try {
    if (-not (Test-Path -LiteralPath (Join-Path $frontendDirectory 'node_modules\vite\bin\vite.js'))) {
        & $npmExecutable install
        if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
    }
} finally { Pop-Location }
Push-Location $backendDirectory
try {
    & $mavenExecutable -q -DskipTests package
    if ($LASTEXITCODE -ne 0) { throw '后端构建失败。' }
} finally { Pop-Location }
$managedProcesses = @{}
function SaveManagedProcess($name, $process) {
    $managedProcesses[$name] = @{ id = $process.Id; started = $process.StartTime.ToUniversalTime().ToString('o') }
    $managedProcesses | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $processFile -Encoding utf8
}
$backendProcess = Start-Process -FilePath $javaExecutable -ArgumentList @('-jar', 'target/assess-report-api-0.1.0.jar') -WorkingDirectory $backendDirectory -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'backend.log') -RedirectStandardError (Join-Path $logDirectory 'backend-error.log') -PassThru
SaveManagedProcess 'backend' $backendProcess
$agentProcess = Start-Process -FilePath $agentPython -ArgumentList @('-m', 'assess_agent') -WorkingDirectory $agentDirectory -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'agent.log') -RedirectStandardError (Join-Path $logDirectory 'agent-error.log') -PassThru
SaveManagedProcess 'agent' $agentProcess
$frontendProcess = Start-Process -FilePath $nodeExecutable -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1') -WorkingDirectory $frontendDirectory -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'frontend.log') -RedirectStandardError (Join-Path $logDirectory 'frontend-error.log') -PassThru
SaveManagedProcess 'frontend' $frontendProcess
Write-Host '前端：http://127.0.0.1:5173'
Write-Host '后端：http://127.0.0.1:8080/api/health'
Write-Host '智能体：http://127.0.0.1:8000/health'
Write-Host '服务正在后台启动，日志保存在 .logs/。'
$servicesReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $backendProcess.Refresh()
    $frontendProcess.Refresh()
    $agentProcess.Refresh()
    if ($backendProcess.HasExited -or $frontendProcess.HasExited -or $agentProcess.HasExited) { throw '服务启动失败，请查看 .logs/ 中的日志。' }
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/health' -TimeoutSec 1
        $web = Invoke-WebRequest -Uri 'http://127.0.0.1:5173' -UseBasicParsing -TimeoutSec 1
        $agentHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 1
        if ($health.status -eq 'UP' -and $web.StatusCode -eq 200 -and $agentHealth.status -eq 'UP') { $servicesReady = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $servicesReady) { throw '服务尚未就绪，请检查 .logs/ 中的启动日志。' }
Write-Host '前端、后端与智能体服务已就绪，可打开前端地址。'
if ($OpenBrowser) { Start-Process 'http://127.0.0.1:5173' }
