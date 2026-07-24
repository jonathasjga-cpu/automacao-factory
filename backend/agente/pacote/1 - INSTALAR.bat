@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AutoFactory Agente - Instalar
color 0B

set LOG_FILE=%USERPROFILE%\AutoFactory_Agente_install.log
echo. > "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo  AutoFactory Agente - Install Log >> "%LOG_FILE%"
echo  Iniciado: %date% %time% >> "%LOG_FILE%"
echo  PC:       %COMPUTERNAME% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

echo.
echo  ============================================================
echo    AutoFactory Agente - Instalacao
echo  ============================================================
echo.
echo  Log: %LOG_FILE%
echo.
echo  Passos:
echo    1. Verifica Python 3.12+
echo    2. Atualiza pip
echo    3. Instala dependencias (playwright, pandas, openpyxl, httpx, certifi)
echo    4. Verifica que tudo importa OK (fail-fast)
echo.
echo  Nota: NAO instala chromium do Playwright — usamos o Chrome real via CDP.
echo.
timeout /t 2 /nobreak >nul

REM ── ETAPA 1: Verifica Python ─────────────────────────────────
echo [1/4] Verificando Python...
echo [1/4] Verificando Python... >> "%LOG_FILE%"
python --version >> "%LOG_FILE%" 2>&1
if !errorlevel! neq 0 (
    echo  Python nao encontrado. Tentando instalar via winget...
    echo  Python nao encontrado. Tentando winget... >> "%LOG_FILE%"
    where winget >nul 2>&1
    if !errorlevel! neq 0 (
        echo.
        echo  ERRO: Python nao instalado e winget indisponivel.
        echo  Instale Python 3.12+ manualmente: https://www.python.org/downloads/
        echo  Marque "Add Python to PATH" no instalador.
        echo.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements >> "%LOG_FILE%" 2>&1
    if !errorlevel! neq 0 (
        echo  ERRO: winget falhou. Ver log. >> "%LOG_FILE%"
        echo  ERRO ao instalar Python via winget. Ver %LOG_FILE%
        pause
        exit /b 1
    )
    REM Recarrega PATH do registro
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKCU\Environment" /v PATH 2^>nul`) do set "USER_PATH=%%B"
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul`) do set "SYSTEM_PATH=%%B"
    set "PATH=!SYSTEM_PATH!;!USER_PATH!"
)
python --version
python --version >> "%LOG_FILE%" 2>&1
if !errorlevel! neq 0 (
    echo  ERRO: Python instalado mas nao esta no PATH. Reabra este .bat apos reiniciar. >> "%LOG_FILE%"
    echo  ERRO: Python nao esta no PATH. Feche esta janela e abra de novo.
    pause
    exit /b 1
)
echo  OK

REM ── ETAPA 2: pip install ─────────────────────────────────────
REM Sem versoes fixas: deixamos o pip escolher a compativel com a versao
REM de Python instalada. `pandas==2.2.2` nao tinha wheel pra Python 3.13 —
REM travava novas maquinas com "No module named 'pandas'" no motor.
echo.
echo [2/3] Atualizando pip...
echo [2/3] pip upgrade... >> "%LOG_FILE%"
python -m pip install --upgrade pip >> "%LOG_FILE%" 2>&1
echo  OK

echo.
echo [3/4] Instalando dependencias Python (playwright, pandas, openpyxl, httpx, certifi)...
echo [3/4] pip install deps... >> "%LOG_FILE%"
python -m pip install playwright pandas openpyxl httpx certifi >> "%LOG_FILE%" 2>&1
if !errorlevel! neq 0 (
    echo.
    echo  ERRO no pip install. Log detalhado em: %LOG_FILE%
    echo.
    echo  Tentando fallback com --user (instalar apenas pro usuario atual)...
    echo  Fallback --user... >> "%LOG_FILE%"
    python -m pip install --user playwright pandas openpyxl httpx certifi >> "%LOG_FILE%" 2>&1
    if !errorlevel! neq 0 (
        echo.
        echo  ERRO tambem no fallback. Ver %LOG_FILE% para detalhes.
        echo  Se o erro citar "Microsoft Visual C++" ou "wheel build failed",
        echo  instale o Python 3.12 no lugar do 3.13:
        echo    winget install --id Python.Python.3.12
        echo.
        pause
        exit /b 1
    )
)
echo  OK

REM ── ETAPA 4: Skip chromium install ───────────────────────────
echo.
echo [4/4] Playwright configurado (usa Chrome real, nao chromium interno).
echo [4/4] Playwright OK — usando Chrome real via CDP >> "%LOG_FILE%"

REM Verifica que pandas ficou realmente instalado (fail-fast antes de sair OK).
python -c "import pandas, openpyxl, playwright, httpx, certifi" >nul 2>&1
if !errorlevel! neq 0 (
    echo.
    echo  ERRO: pip terminou mas 'import pandas/openpyxl/playwright/httpx/certifi' falhou.
    echo  Provavel: pip instalou num Python diferente do que esta no PATH.
    echo  Ver %LOG_FILE% para diagnostico.
    python -c "import sys; print('Python em uso:', sys.executable)"
    pause
    exit /b 1
)
echo  Dependencias verificadas: pandas, openpyxl, playwright, httpx, certifi.

echo.
echo  ============================================================
echo    INSTALACAO CONCLUIDA
echo  ============================================================
echo.
echo  Proximos passos:
echo    - Feche esta janela.
echo    - Rode "2 - ABRIR CHROME.bat".
echo    - Faca login manualmente no GW.
echo    - Rode "3 - INICIAR AGENTE.bat".
echo.
pause
