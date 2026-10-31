@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Robot MT5 - NE PAS FERMER CETTE FENETRE
echo ============================================
echo  Le robot tourne tant que cette fenetre est ouverte.
echo  Mode (paper ou live) = valeur de "mode" dans config.json
echo  Arret d'urgence: double-clique sur stop_bot.bat
echo ============================================
echo.
ping -n 6 127.0.0.1 >nul
python bot.py
echo.
echo Le robot s'est arrete. Relance ce .bat pour redemarrer.
pause
