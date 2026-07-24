@echo off
chcp 65001 >nul
cd /d "%~dp0"
call :detectpy
if not defined PYEXE (
  echo [X] Python nao encontrado. Rode antes o "1 - INSTALAR.bat".
  pause & exit /b
)
echo ============================================================
echo   AutoFactory Agente - Rodando
echo   Python: %PYEXE%
echo   Deixe esta janela aberta. Ctrl+C para parar.
echo ============================================================
echo.
"%PYEXE%" -u agente_bot.py
echo.
echo Agente encerrado. Feche a janela quando terminar de ler.
pause
exit /b

:detectpy
set "PYEXE="
where python >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE ( for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe" )
if not defined PYEXE ( for /d %%D in ("C:\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe" )
exit /b
