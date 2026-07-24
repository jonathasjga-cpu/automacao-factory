@echo off
chcp 65001 >nul
title AutoFactory Agente - Instalador
cd /d "%~dp0"

rem Wrapper minimo pro instalador de verdade em PowerShell.
rem .bat eh fragil em Windows moderno (encoding, quoting, EnableDelayedExpansion,
rem stub Python da Store, etc). PowerShell tem MUITO mais controle.
rem
rem Este .bat: mostra header, roda .ps1, e SEMPRE pausa no fim mesmo que quebre.
rem Se voce ver a mensagem [X] o log detalhado esta em %USERPROFILE%\autofactory_install.log

echo.
echo ============================================================
echo   AutoFactory Agente - INSTALADOR
echo ============================================================
echo   Log detalhado: %USERPROFILE%\autofactory_install.log
echo.

rem Roda o .ps1 com policy Bypass so pra esta execucao (nao muda config do PC).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set EXITCODE=%errorlevel%

echo.
echo ============================================================
if %EXITCODE% equ 0 (
    echo   PRONTO! Proximos passos:
    echo     2 - ABRIR CHROME.bat   ^(faca login nos sistemas^)
    echo     3 - INICIAR AGENTE.bat ^(deixe aberto^)
) else (
    echo   [X] Instalacao terminou com erro. Ver log:
    echo   %USERPROFILE%\autofactory_install.log
    echo.
    echo   Copie o conteudo do log e mande pro suporte.
)
echo ============================================================
echo.
pause
exit /b %EXITCODE%
