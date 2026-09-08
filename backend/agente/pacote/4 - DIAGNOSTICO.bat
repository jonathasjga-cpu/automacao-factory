@echo off
chcp 65001 >nul
title AutoFactory - Diagnostico do Agente
cd /d "%~dp0"
echo.
echo ============================================================
echo   DIAGNOSTICO DO AGENTE - mande um print desta janela
echo ============================================================
echo.
if not exist "%~dp0agente_config.json" (
    echo   [X] agente_config.json nao esta nesta pasta.
    echo       Rode este arquivo de DENTRO da pasta extraida do agente.
    echo       Pasta atual: %~dp0
    echo.
    pause
    exit /b 1
)
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
