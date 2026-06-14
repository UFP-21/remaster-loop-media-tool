@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv" (
  echo [1/3] Creating venv...
  py -m venv .venv
)

echo [2/3] Activating venv...
call ".venv\Scripts\activate.bat"

echo [3/3] Installing requirements...
pip install -U pip
pip install -r requirements.txt

echo.
echo DONE. Now run RUN_APP.bat
pause