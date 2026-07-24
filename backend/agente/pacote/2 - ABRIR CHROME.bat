@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AutoFactory Agente - Abrir Chrome
color 0A

REM Perfil isolado (nao mistura com o Chrome pessoal do usuario)
set CDP_PROFILE=%~dp0cdp_profile
set CDP_PORT=9222
set GW_URL=https://webtrans.saas2.gwsistemas.com.br/login

echo.
echo  ============================================================
echo    AutoFactory Agente - Abrir Chrome (CDP na porta %CDP_PORT%)
echo  ============================================================
echo.
echo  Perfil isolado: %CDP_PROFILE%
echo.

REM Localiza chrome.exe (tenta 3 caminhos padrao)
set CHROME=
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if not defined CHROME (
    echo  ERRO: chrome.exe nao encontrado nos caminhos padrao.
    echo  Instale o Google Chrome: https://www.google.com/chrome/
    pause
    exit /b 1
)

echo  Chrome: %CHROME%
echo.

REM Verifica se ja existe Chrome na porta 9222 (evita conflito)
netstat -an | findstr ":%CDP_PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel! equ 0 (
    echo  AVISO: ja existe um processo escutando na porta %CDP_PORT%.
    echo  Se for outro Chrome CDP, feche antes de continuar.
    echo.
    choice /m "Continuar mesmo assim"
    if !errorlevel! neq 1 exit /b 0
)

echo  Abrindo Chrome...
echo  Assim que abrir, faca login no GW ^(pagina ja carregada^).
echo  Depois, se usar as factories, entre nos portais delas tambem.
echo.

start "" "%CHROME%" ^
  --remote-debugging-port=%CDP_PORT% ^
  --user-data-dir="%CDP_PROFILE%" ^
  --disable-popup-blocking ^
  --no-first-run ^
  --no-default-browser-check ^
  "%GW_URL%"

echo.
echo  Chrome aberto. Deixe esta janela aberta e nao feche o Chrome.
echo.
echo  Quando terminar de logar, abra "3 - INICIAR AGENTE.bat".
echo.
pause
