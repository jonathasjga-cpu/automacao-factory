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
echo   Abrindo o Chrome de automacao (perfil isolado, porta 9222).
echo   Faca LOGIN no GW e deixe a janela aberta.
echo ============================================================
start "" "%CHROME%" --remote-debugging-port=9222 --disable-popup-blocking --no-first-run --no-default-browser-check --user-data-dir="%~dp0cdp_profile" "https://webtrans.saas2.gwsistemas.com.br/login"
timeout /t 4 >nul
