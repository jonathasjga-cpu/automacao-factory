@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   AutoFactory Agente - INSTALADOR
echo ============================================================

rem Passo 0: desativa o stub python da Microsoft Store se existir.
rem O stub eh um reparse point em WindowsApps\python*.exe que quando
rem chamado abre a Store e nao instala nada — mas engana o `where`.
if exist "%LocalAppData%\Microsoft\WindowsApps\python.exe" (
  del "%LocalAppData%\Microsoft\WindowsApps\python.exe" >nul 2>&1
)
if exist "%LocalAppData%\Microsoft\WindowsApps\python3.exe" (
  del "%LocalAppData%\Microsoft\WindowsApps\python3.exe" >nul 2>&1
)

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
echo [2/2] Atualizando pip e instalando dependencias (playwright, pandas, openpyxl, httpx, certifi)...
echo       NAO clique dentro desta janela enquanto instala (o Windows pausa o processo).
"%PYEXE%" -m pip install --upgrade pip >nul 2>&1
"%PYEXE%" -m pip install --upgrade playwright pandas openpyxl httpx certifi
if errorlevel 1 (
  echo.
  echo [X] Falha ao instalar as dependencias. Tentando com --user...
  "%PYEXE%" -m pip install --upgrade --user playwright pandas openpyxl httpx certifi
  if errorlevel 1 (
    echo.
    echo [X] Falha tambem com --user. Verifique internet ^& antivirus e rode de novo.
    pause & exit /b
  )
)
echo.
echo Verificando imports...
"%PYEXE%" -c "import pandas, openpyxl, playwright, httpx, certifi; print('OK: pandas', pandas.__version__, '| openpyxl', openpyxl.__version__, '| playwright OK', '| httpx', httpx.__version__)"
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
rem Procura Python REAL. Evita o stub da Microsoft Store — mesmo que
rem `where python` retorne OK, o stub imprime mensagem e nao roda pip
rem de verdade.
set "PYEXE="
rem 1. Instalacoes de usuario (winget/python.org com "just me")
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
rem 2. Instalacao global (root)
if not defined PYEXE ( for /d %%D in ("C:\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe" )
rem 3. Instalacao "all users" no Program Files
if not defined PYEXE ( for /d %%D in ("%ProgramFiles%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe" )
if not defined PYEXE ( for /d %%D in ("%ProgramFiles(x86)%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe" )
rem 4. So confia no `python` do PATH se ele imprimir versao real ("Python 3.X.Y")
if not defined PYEXE (
  for /f "tokens=* usebackq" %%V in (`python --version 2^>^&1`) do (
    echo %%V | findstr /R /C:"^Python 3\." >nul 2>&1
    if not errorlevel 1 set "PYEXE=python"
  )
)
exit /b
