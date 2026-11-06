# Crée le venv et installe les dépendances.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m venv .venv
$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt

Write-Host ""
Write-Host "Installation terminée." -ForegroundColor Green
Write-Host "1) .\.venv\Scripts\Activate.ps1"
Write-Host "2) python -m fortnite_auto_replay.tools.capture_templates"
Write-Host "3) .\run.ps1 --dry-run"
