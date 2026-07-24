@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AutoFactory Agente - Rodando
color 0E

set BOT=%~dp0agente\agente_bot.py
set CFG=%~dp0agente\agente_config.json
set LOG=%USERPROFILE%\AutoFactory_Agente.log

echo.
echo  ============================================================
echo    AutoFactory Agente - Iniciando
echo  ============================================================
echo.

REM ── Verifica arquivos essenciais ─────────────────────────────
if not exist "%BOT%" (
    echo  ERRO: %BOT% nao encontrado.
    echo  Extraia o zip por completo antes de rodar este .bat.
    goto :seguraJanela
)
if not exist "%CFG%" (
    echo  ERRO: %CFG% nao encontrado.
    echo  O agente_config.json com panel_url e token deve estar junto.
    echo  Re-baixe o pacote no painel do AutoFactory.
    goto :seguraJanela
)

REM ── Verifica Python ─────────────────────────────────────────
python --version >nul 2>&1
if !errorlevel! neq 0 (
    echo  ERRO: Python nao esta no PATH.
    echo  Rode "1 - INSTALAR.bat" primeiro (ou reabra o cmd apos instalar).
    goto :seguraJanela
)

echo  Python em uso:
python -c "import sys; print('    Executavel:', sys.executable); print('    Versao:   ', sys.version.split()[0])"
echo.

REM ── Verifica que as deps subiram ────────────────────────────
python -c "import pandas, openpyxl, playwright, httpx, certifi" 2>nul
if !errorlevel! neq 0 (
    echo  ERRO: dependencias Python nao instaladas.
    echo  Rode "1 - INSTALAR.bat" novamente, ou execute manualmente:
    echo    python -m pip install pandas openpyxl playwright httpx certifi
    goto :seguraJanela
)
echo  Dependencias OK.
echo.

REM ── Roda o agente ────────────────────────────────────────────
echo  Painel + poll a cada 5s abaixo. Deixe esta janela aberta.
echo  (Log tambem em: %LOG%)
echo  (Ctrl+C para parar)
echo.
echo  ============================================================
echo.

REM -u = unbuffered (ver output em tempo real).
REM Pipe pra tee via PowerShell garante que a mesma saida vai pra tela E pro arquivo.
powershell -NoProfile -Command "& { python -u '%BOT%' 2>&1 | Tee-Object -FilePath '%LOG%' }"
set EXITCODE=!errorlevel!

echo.
echo  ============================================================
echo  Agente encerrado. Exit code: !EXITCODE!
echo  Log completo em: %LOG%
echo  ============================================================

:seguraJanela
echo.
echo  A janela ficara aberta para voce ler eventuais mensagens acima.
echo  Feche manualmente (X no canto, ou pressione uma tecla).
echo.
pause >nul
REM Fallback: se pause falhar (stdin fechado), segura mais 1h.
timeout /t 3600 /nobreak >nul
exit /b !EXITCODE!
