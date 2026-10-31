@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Passer en argent reel
python passer_en_live.py
echo.
pause
