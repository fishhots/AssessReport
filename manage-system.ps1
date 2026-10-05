param([Parameter(Mandatory = $true)][ValidateSet('Start', 'Stop')][string]$Action)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
# Both buttons share a lock, so a second click cannot launch another set of services.
$taskPathBytes = [Text.Encoding]::UTF8.GetBytes($PSScriptRoot.ToLowerInvariant())
$taskHasher = [Security.Cryptography.SHA256]::Create()
try { $taskPathHash = [BitConverter]::ToString($taskHasher.ComputeHash($taskPathBytes)).Replace('-', '') }
finally { $taskHasher.Dispose() }
$taskMutex = New-Object Threading.Mutex($false, ('Local\AssessReport-' + $taskPathHash))
$taskLockHeld = $false
try {
    try { $taskLockHeld = $taskMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $taskLockHeld = $true }
    if (-not $taskLockHeld) {
        Write-Host '系统正在启动或停止，请稍候。'
        exit 0
    }
    if ($Action -eq 'Start') {
        Write-Host '正在启动教学质量报告智能体，请稍候……'
        & (Join-Path $PSScriptRoot 'start-dev.ps1') -OpenBrowser
    } else {
        Write-Host '正在停止教学质量报告智能体……'
        & (Join-Path $PSScriptRoot 'stop-dev.ps1')
    }
    exit 0
} catch {
    Write-Host ('操作失败：' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('运行日志：' + (Join-Path $PSScriptRoot '.logs'))
    exit 1
} finally {
    if ($taskLockHeld) { $taskMutex.ReleaseMutex() }
    $taskMutex.Dispose()
}
