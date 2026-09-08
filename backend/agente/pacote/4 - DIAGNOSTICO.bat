@echo off
chcp 65001 >nul
title AutoFactory - Diagnostico do Agente
cd /d "%~dp0"

rem ── Guarda: rodou de dentro do ZIP? ─────────────────────────────
rem Sem blocos `if (...)`: o cmd expande %~dp0 ao PARSEAR o bloco, e um
rem ")" no nome da pasta — "AutoFactory-Agente (1)", que o Windows cria
rem ao baixar o zip duas vezes — fechava o bloco antes da hora.
if exist "%~dp0agente_config.json" goto :_viz_diag
echo.
echo   [X] agente_config.json nao esta ao lado deste arquivo.
echo.
echo       Se voce clicou neste .bat de dentro do .zip, extraia o
echo       .zip primeiro e rode de dentro da pasta extraida.
echo.
echo       Pasta de onde isto rodou:
echo       "%~dp0"
echo.
pause
exit /b 1
:_viz_diag

rem ── Guarda: pasta de rede (UNC) ─────────────────────────────────
set "_DIR=%~dp0"
if not "%_DIR:~0,2%"=="\\" goto :_viz_diag_local
echo.
echo   [X] PASTA DE REDE NAO FUNCIONA
echo.
echo       Copie a pasta do agente para o computador e rode de la.
echo       Pasta atual: "%_DIR%"
echo.
pause
exit /b 1
:_viz_diag_local

echo.
echo ============================================================
echo   DIAGNOSTICO DO AGENTE - mande um print desta janela
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$c = Get-Content '%~dp0agente_config.json' -Raw | ConvertFrom-Json;" ^
  "Write-Host ('  Painel : ' + $c.panel_url);" ^
  "Write-Host ('  Versao : ' + $c.versao);" ^
  "Write-Host ('  Token  : ' + $c.token.Length + ' caracteres');" ^
  "Write-Host '';" ^
  "try {" ^
  "  $r = Invoke-RestMethod -Uri ($c.panel_url + '/api/agente/proximo')" ^
  "        -Headers @{ Authorization = ('Bearer ' + $c.token) } -TimeoutSec 20;" ^
  "  Write-Host '  [OK] O PAINEL ACEITOU ESTE AGENTE.' -ForegroundColor Green;" ^
  "  Write-Host '       Se o painel mostra offline, e so deixar o'; " ^
  "  Write-Host '       3 - INICIAR AGENTE.bat aberto.'" ^
  "} catch {" ^
  "  $cod = 0; if ($_.Exception.Response) { $cod = [int]$_.Exception.Response.StatusCode };" ^
  "  if ($cod -eq 401 -or $cod -eq 403) {" ^
  "    Write-Host '  [X] TOKEN RECUSADO (HTTP 401).' -ForegroundColor Red;" ^
  "    Write-Host '      Este pacote envelheceu. Baixe o agente de novo';" ^
  "    Write-Host '      no painel, extraia e rode o 3 - INICIAR AGENTE.'" ^
  "  } elseif ($cod -gt 0) {" ^
  "    Write-Host ('  [X] O painel respondeu HTTP ' + $cod) -ForegroundColor Red" ^
  "  } else {" ^
  "    Write-Host '  [X] NAO CONSEGUI FALAR COM O PAINEL.' -ForegroundColor Red;" ^
  "    Write-Host '      Parece bloqueio de rede/firewall/proxy.';" ^
  "    Write-Host ('      Detalhe: ' + $_.Exception.Message)" ^
  "  }" ^
  "}"
echo.
echo ============================================================
pause
