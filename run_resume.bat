@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Journal des trades
python resume.py
echo.
pause
