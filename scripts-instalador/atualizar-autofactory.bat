@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AutoFactory - Atualizacao
color 0E

set REPO_USER=jonathasjga-cpu
set REPO_NAME=automacao-factory
set ZIP_URL=https://github.com/%REPO_USER%/%REPO_NAME%/archive/refs/heads/main.zip
set INSTALL_DIR=%USERPROFILE%\AutoFactory
set APP_DIR=%INSTALL_DIR%\app
set BACKUP_DIR=%INSTALL_DIR%\backup_%date:~6,4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%
set BACKUP_DIR=%BACKUP_DIR: =0%

echo.
echo  ============================================================
echo    AUTOFACTORY - Atualizacao
echo  ============================================================
echo.

if not exist "%APP_DIR%" (
    color 0C
    echo  [ERRO] AutoFactory nao esta instalado em %APP_DIR%.
    echo  Use 'instalar-autofactory.bat' primeiro.
    pause
    exit /b 1
)

REM ── ETAPA 1: Para o servidor se estiver rodando ──────────────
echo [1/5] Parando servidor (se estiver rodando)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

REM ── ETAPA 2: Baixa nova versao ───────────────────────────────
echo.
echo [2/5] Baixando ultima versao do GitHub...
set ZIP_FILE=%TEMP%\autofactory_update.zip
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%ZIP_URL%' -OutFile '%ZIP_FILE%' -UseBasicParsing } catch { Write-Host 'ERRO_DOWNLOAD'; exit 1 }"
if %errorlevel% neq 0 (
    color 0C
    echo  [ERRO] Falha ao baixar nova versao. Verifique sua conexao.
    pause
    exit /b 1
)

REM ── ETAPA 3: Extrai pra pasta temporaria ─────────────────────
echo.
echo [3/5] Extraindo nova versao...
set TEMP_EXTRACT=%INSTALL_DIR%\_update_temp
if exist "%TEMP_EXTRACT%" rmdir /s /q "%TEMP_EXTRACT%"
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%TEMP_EXTRACT%' -Force"

REM ── ETAPA 4: Substitui codigo (mantem .venv e dados) ─────────
echo.
echo [4/5] Aplicando atualizacao...
echo    - Mantendo ambiente virtual (.venv) e suas configuracoes
echo    - Substituindo codigo: backend, frontend, requirements, scripts

REM Pega a pasta extraida (vai ter um nome tipo automacao-factory-main)
set NEW_CODE_DIR=
for /d %%D in ("%TEMP_EXTRACT%\*") do (
    set NEW_CODE_DIR=%%D
)

if "%NEW_CODE_DIR%"=="" (
    color 0C
    echo  [ERRO] Estrutura do zip baixado nao reconhecida.
    pause
    exit /b 1
)

REM Substitui pastas/arquivos do codigo (sem mexer em .venv ou em dados)
robocopy "%NEW_CODE_DIR%\backend"  "%APP_DIR%\backend"  /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul
robocopy "%NEW_CODE_DIR%\frontend" "%APP_DIR%\frontend" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul
copy /Y "%NEW_CODE_DIR%\requirements.txt" "%APP_DIR%\requirements.txt" >nul 2>&1
copy /Y "%NEW_CODE_DIR%\Dockerfile"        "%APP_DIR%\Dockerfile"        >nul 2>&1
copy /Y "%NEW_CODE_DIR%\railway.toml"      "%APP_DIR%\railway.toml"      >nul 2>&1
copy /Y "%NEW_CODE_DIR%\CLAUDE.md"         "%APP_DIR%\CLAUDE.md"         >nul 2>&1

REM Limpa
rmdir /s /q "%TEMP_EXTRACT%" 2>nul
del "%ZIP_FILE%" 2>nul

REM ── ETAPA 5: Atualiza dependencias se mudaram ────────────────
echo.
echo [5/5] Verificando dependencias...
cd /d "%APP_DIR%"
call ".venv\Scripts\activate.bat"
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo  [AVISO] Falha ao atualizar bibliotecas. Veja mensagens acima.
)

REM ── Conclusao ────────────────────────────────────────────────
echo.
color 0A
echo  ============================================================
echo    ATUALIZACAO CONCLUIDA!
echo  ============================================================
echo.
echo  Suas credenciais e historico foram preservados.
echo.
echo  Para iniciar:
echo    Clique 2x no atalho 'AutoFactory' na area de trabalho
echo.
pause
