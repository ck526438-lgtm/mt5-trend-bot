@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Creation du fichier STOP: le robot s'arrete a son prochain cycle (max 3 secondes).
echo Les positions deja ouvertes restent avec leur stop-loss chez le broker.
type nul > STOP
echo Fait.
pause
