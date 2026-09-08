@echo off
chcp 65001 >nul
title AutoFactory Agente - Instalador
cd /d "%~dp0"

rem ── Guarda: rodou de dentro do ZIP? ─────────────────────────────
rem Dando duplo clique num .bat que esta DENTRO do .zip, o WinRAR/7-Zip
rem extrai SO o .bat pra uma pasta temporaria e deixa os vizinhos no
rem compactado. O script quebrava com "o arquivo nao existe".
rem
rem IMPORTANTE: sem blocos `if (...)` aqui. O cmd expande %~dp0 ao PARSEAR
rem o bloco, entao um ")" no nome da pasta — "AutoFactory-Agente (1)",
rem que e' o que o Windows cria ao baixar o zip duas vezes — fechava o
rem bloco antes da hora e a janela morria com "\ foi inesperado".
if exist "%~dp0install.ps1" goto :_viz_inst
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
echo     3^) "Extrair tudo..."
echo     4^) Abra a PASTA extraida e rode o .bat de dentro dela
echo.
echo   Esta execucao veio de:
echo   "%~dp0"
echo.
pause
exit /b 1
:_viz_inst


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
rem Sem blocos `if (...)` daqui pra baixo: eles imprimem caminhos, e um
rem ")" no nome da pasta quebraria o parse e fecharia a janela.
if not %EXITCODE% equ 0 goto :_falhou
echo   PRONTO! Proximos passos:
echo     2 - ABRIR CHROME.bat   ^(faca login nos sistemas^)
echo     3 - INICIAR AGENTE.bat ^(deixe aberto^)
goto :_fim
:_falhou
echo   [X] Instalacao terminou com erro ^(codigo %EXITCODE%^).
echo.
echo   --- DIAGNOSTICO ^(mande este print junto^) ---
ver
powershell -NoProfile -Command "'   PowerShell ' + $PSVersionTable.PSVersion.ToString()" 2>nul
echo    Pasta: "%~dp0"
if not exist "%USERPROFILE%\autofactory_install.log" goto :_sem_log
echo    Log detalhado: "%USERPROFILE%\autofactory_install.log"
echo    ^(abra e mande o conteudo — ele diz o passo exato^)
goto :_fim
:_sem_log
echo    Log: nao encontrado. Mande o print DESTA janela — as
echo    linhas de erro acima sao o que importa.
:_fim
echo ============================================================
echo.
pause
exit /b %EXITCODE%
