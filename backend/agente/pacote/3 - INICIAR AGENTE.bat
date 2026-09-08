@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
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
if exist "%~dp0agente_bot.py" goto :_viz_agente
echo.
echo ============================================================
echo   [X] NAO DA PRA RODAR DE DENTRO DO ZIP
echo ============================================================
echo.
echo   Faltou o arquivo agente_bot.py ao lado deste .bat.
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
:_viz_agente

rem ── Guarda: pasta de rede (UNC) ─────────────────────────────────
rem O cmd.exe NAO aceita \\servidor\pasta como diretorio atual.
set "_DIR=%~dp0"
if not "%_DIR:~0,2%"=="\\" goto :_viz_agente_local
echo.
echo ============================================================
echo   [X] PASTA DE REDE NAO FUNCIONA
echo ============================================================
echo.
echo   Esta pasta esta num caminho de rede:
echo   "%_DIR%"
echo.
echo   O Windows nao permite rodar .bat direto de pasta de rede.
echo   COPIE a pasta do agente para o computador ^(ex: Documentos^)
echo   e rode de la.
echo.
pause
exit /b 1
:_viz_agente_local

call :detectpy
if not defined PYEXE (
    echo [X] Python nao encontrado. Rode antes o "1 - INSTALAR.bat".
    pause
    exit /b 1
)
echo ============================================================
echo   AutoFactory Agente - Rodando
echo   Python: !PYEXE!
echo   Deixe esta janela aberta. Ctrl+C para parar.
echo ============================================================
echo.
"!PYEXE!" -u agente_bot.py
echo.
echo Agente encerrado. Feche a janela quando terminar de ler.
pause
exit /b 0

:detectpy
set "PYEXE="
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /d %%D in ("C:\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /d %%D in ("%ProgramFiles%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /d %%D in ("%ProgramFiles(x86)%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE (
    python --version 2>nul | findstr /R /C:"^Python 3\." >nul 2>&1
    if !errorlevel! equ 0 set "PYEXE=python"
)
exit /b
