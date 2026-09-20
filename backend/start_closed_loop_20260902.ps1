$ErrorActionPreference = "Stop"

# Versioned launcher: the original start scripts and app.main remain intact.
$backendRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $backendRoot
$projectRoot = Split-Path -Parent $backendRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonCommand = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { "python" }

# The backend settings file is normally injected by scripts/dev.mjs.  Load the
# same project-root .env here so the dated launcher uses the configured HaS
# Text runtime, remote URL, model, and API key instead of silently falling
# back to the local 127.0.0.1:8080 default.
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

if (-not $env:CLOSED_LOOP_ENABLED) {
    $env:CLOSED_LOOP_ENABLED = "1"
}
if (-not $env:CLOSED_LOOP_MAX_ROUNDS) {
    $env:CLOSED_LOOP_MAX_ROUNDS = "3"
}

& $pythonCommand -m uvicorn app.main_20260902:app --host 0.0.0.0 --port 8000
