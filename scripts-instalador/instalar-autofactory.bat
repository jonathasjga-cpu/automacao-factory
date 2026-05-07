@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AutoFactory - Instalacao
color 0B

REM ============================================================
REM AutoFactory — Instalador (com log e tratamento de erro)
REM ============================================================

set REPO_USER=jonathasjga-cpu
set REPO_NAME=automacao-factory
set ZIP_URL=https://github.com/%REPO_USER%/%REPO_NAME%/archive/refs/heads/main.zip
set INSTALL_DIR=%USERPROFILE%\AutoFactory
set APP_DIR=%INSTALL_DIR%\app
set LOG_FILE=%USERPROFILE%\AutoFactory_install.log

REM Inicia log
echo. > "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo  AutoFactory Install Log >> "%LOG_FILE%"
echo  Iniciado: %date% %time% >> "%LOG_FILE%"
echo  Usuario: %USERNAME% >> "%LOG_FILE%"
echo  PC:      %COMPUTERNAME% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

echo.
echo  ============================================================
echo    AUTOFACTORY - Instalacao
echo  ============================================================
echo.
echo  Pasta de destino: %INSTALL_DIR%
echo  Log:              %LOG_FILE%
echo.
echo  Aguarde, instalando automaticamente...
echo  (Esta janela NAO vai fechar — voce vai ver "INSTALACAO CONCLUIDA"
echo   no final ou uma mensagem de erro com instrucoes.)
echo.
timeout /t 3 /nobreak >nul

REM ── ETAPA 1: Verifica/instala Python ─────────────────────────
echo [1/6] Verificando Python...
echo [1/6] Verificando Python... >> "%LOG_FILE%"
python --version >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo  Python nao encontrado. Instalando via winget...
    echo  Python nao encontrado. Tentando winget... >> "%LOG_FILE%"
    where winget >nul 2>&1
    if !errorlevel! neq 0 (
        echo  ERRO: winget nao disponivel neste Windows >> "%LOG_FILE%"
        goto :erro_python
    )
    winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements >> "%LOG_FILE%" 2>&1
    if !errorlevel! neq 0 (
        echo  ERRO: winget falhou ao instalar Python >> "%LOG_FILE%"
        goto :erro_python
    )
    echo  Python instalado. Atualizando PATH...
    REM Recarrega PATH do registro
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKCU\Environment" /v PATH 2^>nul`) do set "USER_PATH=%%B"
    for /f "usebackq tokens=2,*" %%A in (`reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul`) do set "SYSTEM_PATH=%%B"
    set "PATH=%SYSTEM_PATH%;%USER_PATH%"
)
python --version
python --version >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo  ERRO: Python instalado mas nao esta no PATH >> "%LOG_FILE%"
    goto :erro_python_path
)
echo  OK
echo. >> "%LOG_FILE%"

REM ── ETAPA 2: Cria pastas ─────────────────────────────────────
echo.
echo [2/6] Criando pastas...
echo [2/6] Criando pastas... >> "%LOG_FILE%"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if exist "%APP_DIR%" (
    echo.
    echo  AVISO: ja existe instalacao em %APP_DIR%
    echo  AVISO: ja existe instalacao em %APP_DIR% >> "%LOG_FILE%"
    echo  Para atualizar use 'atualizar-autofactory.bat'
    echo  Para reinstalar do zero, apague a pasta %APP_DIR% manualmente
    goto :final_aviso
)
echo  OK >> "%LOG_FILE%"

REM ── ETAPA 3: Baixa o codigo ──────────────────────────────────
echo.
echo [3/6] Baixando codigo do GitHub...
echo [3/6] Baixando codigo... >> "%LOG_FILE%"
set ZIP_FILE=%TEMP%\autofactory_install.zip
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%ZIP_URL%' -OutFile '%ZIP_FILE%' -UseBasicParsing -ErrorAction Stop; Write-Host 'OK' } catch { Write-Host 'ERRO_DOWNLOAD'; Write-Host $_.Exception.Message; exit 1 }" >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 goto :erro_download
echo  OK
echo  OK >> "%LOG_FILE%"

REM ── ETAPA 4: Extrai ─────────────────────────────────────────
echo.
echo [4/6] Extraindo arquivos...
echo [4/6] Extraindo arquivos... >> "%LOG_FILE%"
if exist "%INSTALL_DIR%\_temp" rmdir /s /q "%INSTALL_DIR%\_temp"
powershell -NoProfile -Command "try { Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%INSTALL_DIR%\_temp' -Force -ErrorAction Stop; Write-Host 'OK' } catch { Write-Host 'ERRO_EXTRACT'; Write-Host $_.Exception.Message; exit 1 }" >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 goto :erro_extract

for /d %%D in ("%INSTALL_DIR%\_temp\*") do (
    move "%%D" "%APP_DIR%" >nul 2>&1
)
rmdir /s /q "%INSTALL_DIR%\_temp" 2>nul
del "%ZIP_FILE%" 2>nul
echo  OK
echo  OK >> "%LOG_FILE%"

REM ── ETAPA 5: Cria venv e instala dependencias ────────────────
echo.
echo [5/6] Instalando dependencias Python (3-5 minutos)...
echo [5/6] Instalando dependencias... >> "%LOG_FILE%"
cd /d "%APP_DIR%"
echo    - Criando ambiente virtual...
python -m venv .venv >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 goto :erro_venv

echo    - Atualizando pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet >> "%LOG_FILE%" 2>&1

echo    - Instalando bibliotecas (pode demorar)...
".venv\Scripts\pip.exe" install -r requirements.txt >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 goto :erro_pip

echo    - Instalando navegador Chromium (2-3 minutos)...
".venv\Scripts\python.exe" -m playwright install chromium >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo  AVISO: Chromium nao instalou — algumas factories podem falhar >> "%LOG_FILE%"
)
echo  OK
echo  OK >> "%LOG_FILE%"

REM ── ETAPA 6: Cria atalho ─────────────────────────────────────
echo.
echo [6/6] Criando atalho na area de trabalho...
echo [6/6] Criando atalho... >> "%LOG_FILE%"
set DESKTOP=%USERPROFILE%\Desktop
set ATALHO=%DESKTOP%\AutoFactory.lnk
set INICIAR_BAT=%INSTALL_DIR%\iniciar.bat

REM Copia o iniciar.bat pra dentro de INSTALL_DIR (caso ele tenha rodado de outra pasta)
copy /Y "%~dp0iniciar.bat" "%INICIAR_BAT%" >> "%LOG_FILE%" 2>&1
copy /Y "%~dp0atualizar-autofactory.bat" "%INSTALL_DIR%\atualizar-autofactory.bat" >> "%LOG_FILE%" 2>&1

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%ATALHO%'); $s.TargetPath = '%INICIAR_BAT%'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.IconLocation = 'shell32.dll,138'; $s.Description = 'AutoFactory'; $s.Save()" >> "%LOG_FILE%" 2>&1

if not exist "%ATALHO%" (
    echo  AVISO: atalho nao criado em %ATALHO% >> "%LOG_FILE%"
)
echo  OK >> "%LOG_FILE%"

REM ── Conclusao ────────────────────────────────────────────────
echo.
color 0A
echo  ============================================================
echo    INSTALACAO CONCLUIDA COM SUCESSO!
echo  ============================================================
echo. >> "%LOG_FILE%"
echo  CONCLUIDO COM SUCESSO em %date% %time% >> "%LOG_FILE%"
echo.
echo  AutoFactory instalado em: %APP_DIR%
echo.
echo  COMO USAR:
echo    1. Clique 2x no atalho 'AutoFactory' na area de trabalho
echo    2. OU execute %INSTALL_DIR%\iniciar.bat
echo.
echo  COMO ATUALIZAR (quando o admin avisar):
echo    Execute %INSTALL_DIR%\atualizar-autofactory.bat
echo.
echo  Log salvo em: %LOG_FILE%
echo.
pause
exit /b 0

REM ============================================================
REM Tratamento de erros
REM ============================================================

:erro_python
color 0C
echo.
echo  ============================================================
echo   ERRO: PYTHON NAO PODE SER INSTALADO AUTOMATICAMENTE
echo  ============================================================
echo.
echo  O Windows neste PC nao tem 'winget' (gerenciador de pacotes)
echo  ou nao conseguiu instalar Python automaticamente.
echo.
echo  SOLUCAO:
echo    1. Baixe Python manualmente em: https://www.python.org/downloads/
echo    2. Marque a opcao "Add Python to PATH" durante a instalacao
echo    3. Reinicie o computador
echo    4. Rode 'instalar-autofactory.bat' de novo
echo.
echo  Log do erro: %LOG_FILE%
echo.
pause
exit /b 1

:erro_python_path
color 0C
echo.
echo  ============================================================
echo   ERRO: PYTHON INSTALADO MAS NAO ESTA NO PATH
echo  ============================================================
echo.
echo  Python foi instalado mas nao esta acessivel no terminal.
echo.
echo  SOLUCAO: Reinicie o computador e rode este instalador novamente.
echo.
echo  Log do erro: %LOG_FILE%
echo.
pause
exit /b 1

:erro_download
color 0C
echo.
echo  ============================================================
echo   ERRO: NAO FOI POSSIVEL BAIXAR O CODIGO
echo  ============================================================
echo.
echo  Pode ser:
echo   - Sem conexao com internet
echo   - Firewall corporativo bloqueando github.com
echo   - Antivirus bloqueando o download
echo.
echo  TESTE: Abra https://github.com/%REPO_USER%/%REPO_NAME% no
echo         seu navegador. Se abrir, o problema e firewall/antivirus.
echo.
echo  Log do erro: %LOG_FILE%
echo.
pause
exit /b 1

:erro_extract
color 0C
echo.
echo  ============================================================
echo   ERRO: NAO FOI POSSIVEL EXTRAIR O ARQUIVO
echo  ============================================================
echo.
echo  Pode ser:
echo   - Antivirus bloqueando arquivos baixados
echo   - Pasta de destino bloqueada
echo.
echo  Log do erro: %LOG_FILE%
echo.
pause
exit /b 1

:erro_venv
color 0C
echo.
echo  ============================================================
echo   ERRO: NAO FOI POSSIVEL CRIAR AMBIENTE PYTHON
echo  ============================================================
echo.
echo  Pode ser problema de permissao na pasta %APP_DIR%
echo.
echo  Log do erro: %LOG_FILE%
echo.
pause
exit /b 1

:erro_pip
color 0C
echo.
echo  ============================================================
echo   ERRO: NAO FOI POSSIVEL INSTALAR BIBLIOTECAS
echo  ============================================================
echo.
echo  Pode ser:
echo   - Internet caiu durante o download
echo   - Antivirus bloqueando pip
echo.
echo  Log do erro: %LOG_FILE%
echo.
pause
exit /b 1

:final_aviso
color 0E
echo.
pause
exit /b 0
