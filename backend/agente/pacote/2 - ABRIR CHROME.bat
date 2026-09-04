@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
rem ── Guarda: rodou de dentro do ZIP? ─────────────────────────────
rem Dando duplo clique num .bat que esta DENTRO do .zip, o WinRAR/7-Zip
rem extrai SO o .bat pra uma pasta temporaria (Temp\Rar$DIa...) e deixa os
rem vizinhos no compactado. O script entao quebra com "o arquivo nao
rem existe", mensagem que nao ajuda ninguem a entender o que fazer.
if not exist "%~dp0agente_config.json" (
    echo.
    echo ============================================================
    echo   [X] NAO DA PRA RODAR DE DENTRO DO ZIP
    echo ============================================================
    echo.
    echo   Faltou o arquivo agente_config.json ao lado deste .bat.
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

rem =================================================
rem Se voce quiser usar um Chrome portable (recomendado: 149)
rem pra evitar bug de trava do Chrome 150 com CDP, extraia o
rem Chrome portable dentro da pasta do agente e ele sera
rem detectado automaticamente:
rem   %~dp0chrome_portable\chrome.exe
rem =================================================
set "CHROME="
if exist "%~dp0chrome_portable\chrome.exe" set "CHROME=%~dp0chrome_portable\chrome.exe"
if not defined CHROME if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined CHROME (
    echo [X] Chrome nao encontrado nos caminhos padrao.
    echo     Instale o Google Chrome, ou abra manualmente com:
    echo     chrome.exe --remote-debugging-port=9222 --user-data-dir="%~dp0cdp_profile"
    pause
    exit /b 1
)
echo ============================================================
echo   Abrindo o Chrome de automacao (perfil isolado, porta 9222)
echo   com abas de: GW, Firma, FluxAsset, GC.
echo   Faca LOGIN em cada uma se pedir (uma vez so — perfil salvo).
echo ============================================================
start "" "!CHROME!" --remote-debugging-port=9222 --disable-popup-blocking --no-first-run --no-default-browser-check --user-data-dir="%~dp0cdp_profile" "https://webtrans.saas2.gwsistemas.com.br/login" "https://intrafac777.firmasa.com/Factadebentures/login" "https://portal.fluxasset.com.br/Factaconsult/login" "https://app.sifacweb.com.br/gcsecuritizadora/Login"
timeout /t 4 >nul
exit /b 0
