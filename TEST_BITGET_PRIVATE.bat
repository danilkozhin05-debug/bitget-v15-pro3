@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo .venv not found. Run START_AUTO_REAL.bat once first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" private_test.py
pause
