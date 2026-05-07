@echo off
chcp 65001 >nul
title AutoFactory
color 0B

set INSTALL_DIR=%USERPROFILE%\AutoFactory
set APP_DIR=%INSTALL_DIR%\app

if not exist "%APP_DIR%" (
    color 0C
    echo.
    echo  [ERRO] AutoFactory nao esta instalado.
    echo  Execute primeiro o 'instalar-autofactory.bat'.
    echo.
    pause
    exit /b 1
)

REM ── Verifica se ja esta rodando ──────────────────────────────
netstat -aon | findstr ":8000 " >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo  AutoFactory ja esta rodando.
    echo  Abrindo no navegador...
    start http://localhost:8000
    timeout /t 3 /nobreak >nul
    exit /b 0
)

REM ── Inicia o servidor ────────────────────────────────────────
echo.
echo  ============================================================
echo    AUTOFACTORY
echo  ============================================================
echo.
echo  Iniciando servidor local...
echo  (Esta janela precisa ficar aberta enquanto voce usa o sistema)
echo.

cd /d "%APP_DIR%"

REM Inicia em background e abre browser apos 4s
start /b "" ".venv\Scripts\python.exe" "backend\main.py"

echo  Aguardando servidor iniciar (4s)...
timeout /t 4 /nobreak >nul

echo  Abrindo AutoFactory no navegador...
start http://localhost:8000

echo.
echo  ------------------------------------------------------------
echo  ATENCAO: NAO FECHE ESTA JANELA enquanto estiver usando.
echo  Para encerrar o sistema, feche esta janela ou pressione Ctrl+C.
echo  ------------------------------------------------------------
echo.

REM Mantem a janela aberta enquanto o servidor roda
:loop
timeout /t 60 /nobreak >nul
netstat -aon | findstr ":8000 " >nul 2>&1
if %errorlevel% equ 0 goto loop

echo.
echo  Servidor encerrado.
pause
