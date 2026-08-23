@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul
title Bitget V15 PRO - PAPER SIGNALS
color 0B
cls
echo ============================================================
echo   BITGET V15 PRO - PAPER / SIGNAL ONLY
echo ============================================================
echo   REAL MARKET DATA   ^|   NO REAL ORDERS   ^|   AI LEARNING
echo   Folder: %CD%
echo ============================================================
echo.
set PAPER_TRADING=true
set AUTO_REAL=false
set LIVE_REAL=false
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -u bot.py
) else (
  python -u bot.py
)
echo.
echo Bot stopped. Press any key...
pause >nul
