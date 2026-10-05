$ErrorActionPreference = 'Stop'
$processFile = Join-Path $PSScriptRoot '.logs\dev-processes.json'
if (-not (Test-Path -LiteralPath $processFile)) { Write-Host '没有找到由启动脚本记录的服务。'; return }
$record = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
foreach ($service in @($record.backend, $record.frontend, $record.agent)) {
    if (-not $service) { continue }
    $process = Get-Process -Id $service.id -ErrorAction SilentlyContinue
    $recordedStart = [datetime]$service.started
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq $recordedStart.ToUniversalTime().Ticks) {
        # Windows venv python.exe may launch a separate interpreter. Stop only
        # the agent child owned by the identity-checked managed launcher.
        if ($service -eq $record.agent) {
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.Id)" | Where-Object { $_.CommandLine -match '-m assess_agent(?:\s|$)' })
            foreach ($child in $children) {
                $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
                if ($childProcess -and $childProcess.StartTime -ge $process.StartTime) {
                    Stop-Process -Id $childProcess.Id
                    $childProcess.WaitForExit(10000) | Out-Null
                }
            }
        }
        $remaining = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($remaining -and $remaining.StartTime.ToUniversalTime().Ticks -eq $recordedStart.ToUniversalTime().Ticks) {
            Stop-Process -Id $remaining.Id -ErrorAction SilentlyContinue
            $remaining.WaitForExit(10000) | Out-Null
        }
    }
}
Remove-Item -LiteralPath $processFile
Write-Host '已停止本项目启动脚本记录的服务。'
