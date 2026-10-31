@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Installation robot MT5
echo Installation des dependances (MetaTrader5, pandas, numpy)...
python -m pip install --upgrade MetaTrader5 pandas numpy
echo.
echo Terminer. Ferme cette fenetre.
pause
