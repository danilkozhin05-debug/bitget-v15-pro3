@echo off
setlocal
chcp 65001 >nul
title Bitget V15 PRO - GitHub Update
cd /d "%~dp0"

echo ============================================================
echo          BITGET V15 PRO - GITHUB AUTO UPDATE
echo ============================================================
echo.
echo This will update the program from your GitHub repository.
echo User data and trading memory will be preserved.
echo.

if not exist "updater.py" (
    echo [ERROR] updater.py not found.
    pause
    exit /b 1
)

python updater.py
if errorlevel 1 (
    echo.
    echo Update failed or was cancelled.
    pause
    exit /b 1
)

echo.
echo Update finished successfully.
pause
