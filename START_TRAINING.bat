@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
title V15 PRO - HISTORICAL TRAINING + ML
color 0B
cls
echo ============================================================
echo   BITGET V15 PRO - HISTORICAL TRAINING + ML
echo   REAL MARKET DATA / NO REAL ORDERS
echo ============================================================
echo.
echo [INFO] Working folder: %CD%
echo [INFO] This downloads public Bitget candles and trains a
echo [INFO] walk-forward-compatible 1m model for LONG and SHORT.
echo [INFO] No private API keys and no real orders are used.
echo.
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON (
  echo [ERROR] Python was not found. Install Python 3.11+ and enable PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  "%PYTHON%" -m venv .venv
  if errorlevel 1 goto :fail
) else echo [1/4] Virtual environment already exists.
echo [2/4] Installing/checking required libraries...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail
echo [3/4] Checking trainer...
".venv\Scripts\python.exe" -m py_compile historical_trainer.py
if errorlevel 1 goto :fail
echo [4/4] Starting historical download + ML training...
echo.
".venv\Scripts\python.exe" -u historical_trainer.py
set "RC=%ERRORLEVEL%"
echo.
echo ============================================================
if "%RC%"=="0" (echo [DONE] Historical training completed.) else (echo [ERROR] Trainer exited with code %RC%.)
echo ============================================================
echo.
echo Files created/updated:
echo   historical_candles.csv
echo   historical_models.json
echo   training_report.json
echo.
pause
exit /b %RC%
:fail
echo.
echo [ERROR] Training setup failed. No trading was started.
pause
exit /b 1
