# Starts the Context X-ray demo on http://localhost:8080 (Windows PowerShell)
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
  python -m venv .venv
  .\.venv\Scripts\pip install -q -r requirements.txt
}
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\uvicorn xray.server:app --port 8080
