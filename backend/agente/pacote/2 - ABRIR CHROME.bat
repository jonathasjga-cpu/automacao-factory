@echo off
chcp 65001 >nul
setlocal
set "CHROME="
for %%P in (
  "C:\Program Files\Google\Chrome\Application\chrome.exe"
  "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do if not defined CHROME if exist %%P set "CHROME=%%~P"
if not defined CHROME (
  echo [X] Chrome nao encontrado nos caminhos padrao.
  echo     Instale o Google Chrome, ou abra manualmente com:
  echo     chrome.exe --remote-debugging-port=9222 --user-data-dir="%~dp0cdp_profile"
  pause
  exit /b
)
echo ============================================================
echo   Abrindo o Chrome de automacao (perfil isolado, porta 9222)
echo   com abas de: GW, Firma, FluxAsset, GC.
echo   Faca LOGIN em cada uma se pedir (uma vez so — perfil salvo).
echo ============================================================
start "" "%CHROME%" ^
  --remote-debugging-port=9222 ^
  --disable-popup-blocking ^
  --no-first-run ^
  --no-default-browser-check ^
  --user-data-dir="%~dp0cdp_profile" ^
  "https://webtrans.saas2.gwsistemas.com.br/login" ^
  "https://intrafac777.firmasa.com/Factadebentures/login" ^
  "https://portal.fluxasset.com.br/Factaconsult/login" ^
  "http://gcrecursos.dyndns.org:9000/FactaConsult"
timeout /t 4 >nul
