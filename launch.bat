@echo off
cd /d "%~dp0"

:: Si tu as un dossier .venv dans ton projet :
if exist ".venv\Scripts\python.exe" (
    echo Lancement avec l'environnement virtuel (.venv)...
    .venv\Scripts\python.exe greg.py
) else (
    :: Sinon, tente le Python global
    echo Lancement avec Python global...
    python greg.py
)

pause