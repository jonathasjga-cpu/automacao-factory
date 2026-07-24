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
echo    2. Instala dependencias (playwright, pandas, openpyxl, httpx, certifi)
echo    3. NAO instala chromium do Playwright (usamos o Chrome real)
echo.
timeout /t 2 /nobreak >nul

REM ── ETAPA 1: Verifica Python ─────────────────────────────────
echo [1/3] Verificando Python...
echo [1/3] Verificando Python... >> "%LOG_FILE%"
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
echo.
echo [2/3] Instalando dependencias Python...
echo [2/3] pip install... >> "%LOG_FILE%"
python -m pip install --upgrade pip >> "%LOG_FILE%" 2>&1
python -m pip install playwright==1.44.0 pandas==2.2.2 openpyxl==3.1.2 httpx==0.27.0 certifi >> "%LOG_FILE%" 2>&1
if !errorlevel! neq 0 (
    echo  ERRO no pip install. Ver %LOG_FILE%
    pause
    exit /b 1
)
echo  OK

REM ── ETAPA 3: Skip chromium install ───────────────────────────
echo.
echo [3/3] Playwright configurado (usa Chrome real, nao chromium interno).
echo [3/3] Playwright OK — usando Chrome real via CDP >> "%LOG_FILE%"

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
