@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "LIVE_REAL=true"
set "AUTO_REAL=true"
cd /d "%~dp0"
title Bitget STRICT V15 PRO - AUTO REAL TRADING

echo ============================================================
echo   BITGET STRICT V15 PRO - AUTO REAL
   echo ============================================================
echo.

echo [1/5] Searching Python 3.12/3.13...
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON (
  echo [ERROR] Python 3.12/3.13 not found.
  echo Install Python from python.org and enable "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Python: %PYTHON%

if not exist ".venv\Scripts\python.exe" (
  echo [2/5] Creating virtual environment...
  "%PYTHON%" -m venv .venv
  if errorlevel 1 goto :fail
) else (
  echo [2/5] Virtual environment already exists.
)

echo [3/5] Installing/checking required libraries...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

if not exist ".env" (
  echo [4/5] Creating .env from .env.example...
  copy /Y ".env.example" ".env" >nul
) else (
  echo [4/5] .env already exists - keeping your settings.
)

echo [5/5] Checking Python and Colorama...
".venv\Scripts\python.exe" -c "import colorama, aiohttp, pandas, numpy, dotenv; print('Dependencies: OK')"
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m py_compile bot.py
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo   AUTO REAL IS ENABLED
 echo   LIVE_REAL=true  AUTO_REAL=true
 echo   REAL ORDERS CAN BE SENT TO BITGET
 echo ============================================================
echo.
".venv\Scripts\python.exe" bot.py
set "RC=%ERRORLEVEL%"
echo.
echo Bot exited with code %RC%.
pause
exit /b %RC%

:fail
echo.
echo [ERROR] Setup failed. No trading was started.
pause
exit /b 1
