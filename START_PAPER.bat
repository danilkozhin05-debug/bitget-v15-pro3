@echo off
setlocal
title Bitget V15 PRO - PAPER / SIGNAL ONLY
echo ================================================
echo   Bitget V15 PRO - PAPER / SIGNAL ONLY
echo   Real market data - NO real orders
echo ================================================
echo.
set LIVE_REAL=false
set AUTO_REAL=false
set PAPER_TRADING=true
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" bot.py
) else (
  python bot.py
)
echo.
echo Bot stopped.
pause
