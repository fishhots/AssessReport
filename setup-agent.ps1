param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
$agentDirectory = Join-Path $PSScriptRoot 'agent-service'
$agentPython = Join-Path $agentDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $agentPython)) {
    if (-not $PythonExecutable) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand -and $pythonCommand.Source -notmatch 'WindowsApps') { $PythonExecutable = $pythonCommand.Source }
    }
    if (-not $PythonExecutable) {
        $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $bundledPython) { $PythonExecutable = $bundledPython }
    }
    if (-not $PythonExecutable) { throw '请安装 Python 3.11 或更高版本，或通过 -PythonExecutable 指定 Python 路径。' }
    & $PythonExecutable -m venv (Join-Path $agentDirectory '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 环境创建失败。' }
}
Push-Location $agentDirectory
try {
    & $agentPython -m pip install -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw '智能体依赖安装失败。' }
    & $agentPython -m pip install -e . --no-deps
    if ($LASTEXITCODE -ne 0) { throw '智能体服务安装失败。' }
    $configPath = Join-Path $agentDirectory '.env'
    if (-not (Test-Path -LiteralPath $configPath)) { Copy-Item -LiteralPath (Join-Path $agentDirectory '.env.example') -Destination $configPath }
} finally { Pop-Location }
Write-Host '智能体环境已准备好。启动服务后，请在网页的模型设置中填写需要使用的模型配置。'
