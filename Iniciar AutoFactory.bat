@echo off
title AutoFactory - Servidor
cd /d "C:\Claude Operações\automacao-factory"
echo Iniciando AutoFactory...
echo.
.venv\Scripts\python.exe backend/main.py
pause
