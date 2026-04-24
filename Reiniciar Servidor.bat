@echo off
title AutoFactory — Reiniciando servidor
echo.
echo Encerrando servidor anterior...

:: Mata processo na porta 8000 se existir
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)

timeout /t 2 /nobreak >nul

echo Iniciando servidor novo...
echo.

cd /d "C:\Claude Operações\automacao-factory"
.venv\Scripts\python.exe backend\main.py

pause
