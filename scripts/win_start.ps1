$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$PORT = if ($env:PORT) { $env:PORT } else { "8010" }
$HOST_ADDR = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }

# create venv if missing
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

# activate
& ".venv\Scripts\Activate.ps1"

# install deps
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

# load .env if present
if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -match "^\s*[^#].*=" } | ForEach-Object {
        $parts = $_ -split "=", 2
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

# ensure data dir
New-Item -ItemType Directory -Force -Path "data" | Out-Null

Write-Host ""
Write-Host "  AI English Lab  →  http://${HOST_ADDR}:${PORT}" -ForegroundColor Green
Write-Host "  Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

python -m uvicorn app:app --reload --host $HOST_ADDR --port $PORT
