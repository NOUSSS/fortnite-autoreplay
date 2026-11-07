@echo off
rem Ouvre le panneau de contrôle du bot, sans console.
rem Python du venv local s'il existe (install.ps1), sinon celui du système.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m fortnite_auto_replay panel %*
) else (
    start "" pythonw -m fortnite_auto_replay panel %*
)
