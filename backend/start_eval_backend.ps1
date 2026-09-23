# 基准评测专用后端启动脚本（与 start_closed_loop_20260902.ps1 同逻辑，端口/轮次可配）
# 用法：
#   powershell -ExecutionPolicy Bypass -File backend\start_eval_backend.ps1 -Port 8002 -Rounds 3
#   powershell -ExecutionPolicy Bypass -File backend\start_eval_backend.ps1 -Port 8002 -Rounds 1
param(
    [int]$Port = 8002,
    [int]$Rounds = 3
)

$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $backendRoot
$projectRoot = Split-Path -Parent $backendRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonCommand = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { "python" }

# 加载项目根 .env（远程 HaS 服务地址 / 模型名 / API Key 等，逻辑同 start_closed_loop_20260902.ps1）
$envFile = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($rawLine in Get-Content -LiteralPath $envFile) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
        $separator = $line.IndexOf("=")
        if ($separator -le 0) { continue }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if (-not [Environment]::GetEnvironmentVariable($name)) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

if (-not $env:CLOSED_LOOP_ENABLED) { $env:CLOSED_LOOP_ENABLED = "1" }
$env:CLOSED_LOOP_MAX_ROUNDS = "$Rounds"

# 数据目录隔离，避免与 dev 栈争用 sqlite
$env:DATA_DIR = Join-Path $env:TEMP "opencode\eval-data"
$env:UPLOAD_DIR = Join-Path $env:DATA_DIR "uploads"
$env:OUTPUT_DIR = Join-Path $env:DATA_DIR "outputs"
New-Item -ItemType Directory -Force -Path $env:DATA_DIR, $env:UPLOAD_DIR, $env:OUTPUT_DIR | Out-Null

Write-Host "[eval] port=$Port rounds=$Rounds data=$env:DATA_DIR"
& $pythonCommand -m uvicorn app.main_20260902:app --host 127.0.0.1 --port $Port
