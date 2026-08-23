@echo off
chcp 65001 >nul
title Bitget V15 PRO 15.3 - PREMIUM PAPER
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PAPER_TRADING=true
set AUTO_REAL=false
set LIVE_REAL=false
python bot.py --paper
pause
