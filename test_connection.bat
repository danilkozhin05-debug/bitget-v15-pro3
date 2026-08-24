@echo off
setlocal
cd /d "%~dp0"
title Bitget STRICT V11 - Connection Test

echo ===== BITGET =====
curl.exe -L --connect-timeout 6 --max-time 10 "https://api.bitget.com/api/v3/market/tickers?category=USDT-FUTURES^&symbol=BTCUSDT"
echo.
echo.
echo ===== NTFY =====
curl.exe -L --connect-timeout 6 --max-time 10 -d "Bitget V11 test notification" "https://ntfy.sh/p2psignalsp2p"
echo.
echo.
echo Если Bitget отвечает JSON с code=00000, интернет до Bitget работает.
echo Если ntfy отвечает 200/JSON, уведомления работают.
pause
