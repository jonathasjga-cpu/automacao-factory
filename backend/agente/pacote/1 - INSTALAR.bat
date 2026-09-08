@echo off
chcp 65001 >nul
title AutoFactory Agente - Instalador
cd /d "%~dp0"

rem ── Guarda: rodou de dentro do ZIP? ─────────────────────────────
rem Dando duplo clique num .bat que esta DENTRO do .zip, o WinRAR/7-Zip
rem extrai SO o .bat pra uma pasta temporaria (Temp\Rar$DIa...) e deixa os
rem vizinhos no compactado. O script entao quebra com "o arquivo nao
rem existe", mensagem que nao ajuda ninguem a entender o que fazer.
if not exist "%~dp0install.ps1" (
    echo.
    echo ============================================================
    echo   [X] NAO DA PRA RODAR DE DENTRO DO ZIP
    echo ============================================================
    echo.
    echo   Faltou o arquivo install.ps1 ao lado deste .bat.
    echo   Isso acontece quando o .bat e aberto direto de dentro do
    echo   arquivo compactado: o descompactador copia so o .bat pra uma
    echo   pasta temporaria e deixa todo o resto para tras.
    echo.
    echo   COMO RESOLVER:
    echo     1^) Feche esta janela.
    echo     2^) Botao direito no AutoFactory-Agente.zip
    echo     3^) "Extrair tudo..." ^(ou "Extrair aqui"^)
    echo     4^) Abra a PASTA extraida e rode o .bat de dentro dela
    echo.
    echo   Esta execucao veio de:
    echo   %~dp0
    echo.
    pause
    exit /b 1
)

rem Wrapper minimo pro instalador de verdade em PowerShell.
rem .bat eh fragil em Windows moderno (encoding, quoting, EnableDelayedExpansion,
rem stub Python da Store, etc). PowerShell tem MUITO mais controle.
rem
rem Este .bat: mostra header, roda .ps1, e SEMPRE pausa no fim mesmo que quebre.
rem Se voce ver a mensagem [X] o log detalhado esta em %USERPROFILE%\autofactory_install.log

echo.
rem ── Guarda: pasta de rede (UNC) ─────────────────────────────────
rem O cmd.exe NAO aceita caminho \servidor\pasta como diretorio atual.
rem O `cd /d` falha, o script segue na pasta errada e nada funciona — e a
rem janela fecha antes de dar tempo de ler o motivo.
set "_DIR=%~dp0"
if "%_DIR:~0,2%"=="\\" (
    echo.
    echo ============================================================
    echo   [X] PASTA DE REDE NAO FUNCIONA
    echo ============================================================
    echo.
    echo   Esta pasta esta num caminho de rede:
    echo   %_DIR%
    echo.
    echo   O Windows nao permite rodar .bat direto de pasta de rede.
    echo   COPIE a pasta do agente para o computador ^(ex: Documentos^)
    echo   e rode de la.
    echo.
    pause
    exit /b 1
)

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
    echo   [X] Instalacao terminou com erro ^(codigo %EXITCODE%^).
    echo.
    echo   --- DIAGNOSTICO ^(mande este print junto^) ---
    ver
    powershell -NoProfile -Command "'   PowerShell ' + $PSVersionTable.PSVersion.ToString()" 2>nul
    echo    Pasta: %~dp0
    if exist "%USERPROFILE%\autofactory_install.log" (
        echo    Log detalhado: %USERPROFILE%\autofactory_install.log
        echo    ^(abra e mande o conteudo — ele diz o passo exato^)
    ) else (
        echo    Log: nao encontrado. Mande o print DESTA janela — as
        echo    linhas de erro acima sao o que importa.
    )
)
echo ============================================================
echo.
pause
exit /b %EXITCODE%
