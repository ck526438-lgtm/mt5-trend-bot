@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Backtest strategie
echo Lance le backtest sur l'historique reel M15 de ton terminal MT5...
echo (le terminal MetaTrader 5 doit etre ouvert et connecte)
echo.
python backtest.py
echo.
pause
