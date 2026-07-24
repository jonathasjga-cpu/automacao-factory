@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AutoFactory Agente - Rodando
color 0E

set BOT=%~dp0agente\agente_bot.py

echo.
echo  ============================================================
echo    AutoFactory Agente - Iniciando
echo  ============================================================
echo.

if not exist "%BOT%" (
    echo  ERRO: %BOT% nao encontrado. Extraia o zip por completo.
    pause
    exit /b 1
)

REM Verifica Python novamente (caso o usuario tenha pulado o 1 - INSTALAR.bat)
python --version >nul 2>&1
if !errorlevel! neq 0 (
    echo  Python nao encontrado. Rode "1 - INSTALAR.bat" primeiro.
    pause
    exit /b 1
)

echo  Rodando agente. Deixe esta janela aberta enquanto usar o painel.
echo  (Ctrl+C para parar)
echo.

python "%BOT%"

echo.
echo  Agente encerrado.
pause
