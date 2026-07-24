@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   AutoFactory Agente - INSTALADOR
echo ============================================================
call :detectpy
if defined PYEXE goto :haspy
echo Python nao encontrado. Vou instalar automaticamente (1-3 min)...
echo.
where winget >nul 2>&1
if %errorlevel%==0 (
  echo [1/2] Instalando Python via winget...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
) else (
  echo [1/2] Baixando o instalador do Python...
  powershell -NoProfile -Command "try{Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile \"$env:TEMP\pyinst.exe\"}catch{exit 1}"
  echo       Instalando (silencioso)...
  "%TEMP%\pyinst.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
)
call :detectpy
if not defined PYEXE (
  echo.
  echo [X] Nao consegui instalar o Python automaticamente.
  echo     Instale manual em https://www.python.org/downloads/  ^(marque "Add to PATH"^) e rode de novo.
  echo.
  pause & exit /b
)
:haspy
echo Usando Python: %PYEXE%
echo [2/2] Instalando dependencias (playwright, pandas, openpyxl, httpx, certifi)...
echo       NAO clique dentro desta janela enquanto instala (o Windows pausa o processo).
"%PYEXE%" -m pip install --upgrade pip >nul 2>&1
"%PYEXE%" -m pip install --upgrade playwright pandas openpyxl httpx certifi
if errorlevel 1 (
  echo.
  echo [X] Falha ao instalar as dependencias. Verifique a internet e rode de novo.
  pause & exit /b
)
echo.
echo Verificando imports...
"%PYEXE%" -c "import pandas, openpyxl, playwright, httpx, certifi; print('OK: pandas', pandas.__version__, '| openpyxl', openpyxl.__version__, '| playwright', playwright.__version__)"
if errorlevel 1 (
  echo.
  echo [X] Deps instaladas mas import falhou.
  pause & exit /b
)
echo.
echo ============================================================
echo   PRONTO! Agora, sempre que for usar:
echo     2 - ABRIR CHROME.bat   (faca login nos sistemas)
echo     3 - INICIAR AGENTE.bat (deixe aberto)
echo ============================================================
pause
exit /b

:detectpy
rem procura um Python REAL (evita o "py" da Store que abre assistente e trava)
set "PYEXE="
where python >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE ( for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe" )
if not defined PYEXE ( for /d %%D in ("C:\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe" )
exit /b
