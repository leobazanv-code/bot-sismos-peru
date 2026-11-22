@echo off
title Monitor Sismico Peru con Alerta ntfy
cd /d "%~dp0"
echo ========================================================
echo Iniciando Monitor Sismico Peru (alertas ntfy)...
echo ========================================================
python bot_sismos.py
pause
