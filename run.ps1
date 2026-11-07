# Lance le bot avec le Python du venv local (créé par install.ps1).
# Usage : .\run.ps1            (démarre en pause, F8 = lancer, F9 = quitter)
#         .\run.ps1 --dry-run  (aucun clic)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    # pas de venv : Python du système, s'il a les dépendances (requirements.txt)
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) {
        Write-Error "Python introuvable : lance d'abord .\install.ps1"
    }
}
$env:PYTHONPATH = Join-Path $root "src"
& $python -m fortnite_auto_replay run @args
exit $LASTEXITCODE
