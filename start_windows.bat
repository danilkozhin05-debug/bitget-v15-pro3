@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Bitget STRICT V15 PRO - REAL DATA - 20 FUTURES

set "PYTHON="
where py >nul 2>nul && set "PYTHON=py"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"

if not defined PYTHON (
  echo [ERROR] Python не найден.
  echo Установи Python 3.12/3.13 с python.org и снова запусти этот файл.
  pause
  exit /b 1
)

echo [1/4] Python: %PYTHON%
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Создаю виртуальное окружение...
  "%PYTHON%" -m venv .venv
  if errorlevel 1 goto :fail
) else (
  echo [2/4] .venv уже существует.
)

if not exist ".env" (
  echo Создаю .env из .env.example...
  copy /Y ".env.example" ".env" >nul
)

echo [3/4] Устанавливаю/проверяю зависимости...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [4/4] Проверяю синтаксис Python...
".venv\Scripts\python.exe" -m py_compile bot.py
if errorlevel 1 (
  echo [ERROR] Ошибка синтаксиса. Запуск отменен.
  pause
  exit /b 1
)

echo.
echo ===== BITGET REAL DATA CHECK =====
curl.exe -L --connect-timeout 6 --max-time 10 "https://api.bitget.com/api/v3/market/candles?category=USDT-FUTURES^&symbol=BTCUSDT^&interval=1m^&type=MARKET^&limit=3"
echo.
echo ===== ЗАПУСК V15 PRO =====
".venv\Scripts\python.exe" bot.py
pause
exit /b 0

:fail
echo [ERROR] Не удалось подготовить Python/зависимости.
pause
exit /b 1
