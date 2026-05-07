@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AutoFactory - Instalacao
color 0B

REM ============================================================
REM AutoFactory — Instalador
REM Baixa o codigo do GitHub, instala Python (se necessario)
REM e configura o ambiente em C:\AutoFactory\app\
REM ============================================================

set REPO_USER=jonathasjga-cpu
set REPO_NAME=automacao-factory
set ZIP_URL=https://github.com/%REPO_USER%/%REPO_NAME%/archive/refs/heads/main.zip
set INSTALL_DIR=%USERPROFILE%\AutoFactory
set APP_DIR=%INSTALL_DIR%\app

echo.
echo  ============================================================
echo    AUTOFACTORY - Instalacao
echo  ============================================================
echo.
echo  Este programa vai instalar o AutoFactory no seu computador.
echo.
echo  - Pasta de instalacao: %INSTALL_DIR%
echo  - Tempo estimado:      5 a 10 minutos
echo  - Internet:            necessaria
echo.
pause

REM ── ETAPA 1: Verifica/instala Python ─────────────────────────
echo.
echo [1/6] Verificando Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  Python nao encontrado. Instalando via winget...
    winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo  [ERRO] Nao foi possivel instalar Python automaticamente.
        echo  Por favor, baixe e instale manualmente em: https://www.python.org/downloads/
        echo  Marque a opcao "Add Python to PATH" durante a instalacao.
        echo.
        pause
        exit /b 1
    )
    echo  Python instalado. Atualizando PATH...
    REM Recarrega PATH sem precisar reabrir terminal
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKCU\Environment" /v PATH 2^>nul`) do set "USER_PATH=%%B"
    set "PATH=%PATH%;%USER_PATH%"
)
python --version
if %errorlevel% neq 0 (
    echo  [ERRO] Python instalado mas nao esta no PATH. Reinicie o computador e rode este instalador novamente.
    pause
    exit /b 1
)

REM ── ETAPA 2: Cria pastas ─────────────────────────────────────
echo.
echo [2/6] Criando pastas...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if exist "%APP_DIR%" (
    echo  AVISO: instalacao anterior detectada em %APP_DIR%.
    echo  Para atualizar use 'atualizar-autofactory.bat'.
    echo  Para reinstalar do zero, apague a pasta %APP_DIR% manualmente.
    pause
    exit /b 1
)

REM ── ETAPA 3: Baixa o codigo do GitHub ────────────────────────
echo.
echo [3/6] Baixando codigo do GitHub...
set ZIP_FILE=%TEMP%\autofactory_install.zip
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%ZIP_URL%' -OutFile '%ZIP_FILE%' -UseBasicParsing } catch { Write-Host 'ERRO_DOWNLOAD: ' $_.Exception.Message; exit 1 }"
if %errorlevel% neq 0 (
    echo.
    echo  [ERRO] Falha ao baixar o codigo. Verifique sua conexao com internet.
    pause
    exit /b 1
)

REM ── ETAPA 4: Extrai ─────────────────────────────────────────
echo.
echo [4/6] Extraindo arquivos...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%INSTALL_DIR%\_temp' -Force"
if %errorlevel% neq 0 (
    echo  [ERRO] Falha ao extrair. Pasta de destino pode estar bloqueada.
    pause
    exit /b 1
)

REM Move o conteudo da pasta extraida (que tem o nome do branch) pra app\
for /d %%D in ("%INSTALL_DIR%\_temp\*") do (
    move "%%D" "%APP_DIR%" >nul
)
rmdir /s /q "%INSTALL_DIR%\_temp" 2>nul
del "%ZIP_FILE%" 2>nul

REM ── ETAPA 5: Cria venv e instala dependencias ────────────────
echo.
echo [5/6] Instalando dependencias Python (pode demorar 3-5 minutos)...
cd /d "%APP_DIR%"
python -m venv .venv
if %errorlevel% neq 0 (
    echo  [ERRO] Falha ao criar ambiente virtual.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"
echo    - Atualizando pip...
python -m pip install --upgrade pip --quiet
echo    - Instalando bibliotecas...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo  [ERRO] Falha ao instalar bibliotecas. Veja mensagens acima.
    pause
    exit /b 1
)
echo    - Instalando navegador Chromium do Playwright (pode demorar 2-3 minutos)...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo  [AVISO] Falha ao instalar Chromium. AutoFactory funcionara mas pode ter problemas.
)

REM ── ETAPA 6: Cria atalho na area de trabalho ─────────────────
echo.
echo [6/6] Criando atalho na area de trabalho...
set DESKTOP=%USERPROFILE%\Desktop
set ATALHO=%DESKTOP%\AutoFactory.lnk
set INICIAR_BAT=%INSTALL_DIR%\iniciar.bat
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%ATALHO%'); $s.TargetPath = '%INICIAR_BAT%'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.IconLocation = 'shell32.dll,138'; $s.Description = 'AutoFactory'; $s.Save()"

REM ── Conclusao ────────────────────────────────────────────────
echo.
color 0A
echo  ============================================================
echo    INSTALACAO CONCLUIDA COM SUCESSO!
echo  ============================================================
echo.
echo  AutoFactory instalado em: %APP_DIR%
echo.
echo  Para iniciar:
echo    1. Clique 2x no atalho 'AutoFactory' na area de trabalho
echo    2. OU execute %INSTALL_DIR%\iniciar.bat
echo.
echo  O sistema vai abrir em http://localhost:8000 no seu navegador.
echo.
echo  Para atualizar quando o admin publicar nova versao:
echo    Execute %INSTALL_DIR%\atualizar-autofactory.bat
echo.
pause
