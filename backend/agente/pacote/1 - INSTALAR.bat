@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   AutoFactory Agente - INSTALADOR
echo ============================================================
echo.

rem Passo 0: desativa o stub python da Microsoft Store se existir.
rem O stub eh um reparse point em WindowsApps\python*.exe que quando
rem chamado abre a Store e nao instala nada.
if exist "%LocalAppData%\Microsoft\WindowsApps\python.exe" del "%LocalAppData%\Microsoft\WindowsApps\python.exe" >nul 2>&1
if exist "%LocalAppData%\Microsoft\WindowsApps\python3.exe" del "%LocalAppData%\Microsoft\WindowsApps\python3.exe" >nul 2>&1

call :detectpy
if defined PYEXE goto haspy

echo Python nao encontrado. Vou instalar automaticamente (1-3 min)...
echo.
where winget >nul 2>&1
if !errorlevel! equ 0 (
    echo [1/2] Instalando Python via winget...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
) else (
    echo [1/2] Baixando o instalador do Python...
    powershell -NoProfile -Command "try{Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile \"$env:TEMP\pyinst.exe\"}catch{exit 1}"
    echo        Instalando (silencioso)...
    "%TEMP%\pyinst.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
)
call :detectpy
if not defined PYEXE (
    echo.
    echo [X] Nao consegui instalar o Python automaticamente.
    echo     Instale manual em https://www.python.org/downloads/  ^(marque "Add to PATH"^) e rode de novo.
    echo.
    pause
    exit /b 1
)

:haspy
echo Usando Python: !PYEXE!
echo.
echo [2/2] Atualizando pip e instalando dependencias...
echo        (playwright, pandas, openpyxl, httpx, certifi)
echo        NAO clique dentro desta janela enquanto instala.
echo.
"!PYEXE!" -m pip install --upgrade pip >nul 2>&1
"!PYEXE!" -m pip install --upgrade playwright pandas openpyxl httpx certifi
if !errorlevel! neq 0 (
    echo.
    echo [X] Falha ao instalar as dependencias. Tentando com --user...
    "!PYEXE!" -m pip install --upgrade --user playwright pandas openpyxl httpx certifi
    if !errorlevel! neq 0 (
        echo.
        echo [X] Falha tambem com --user. Verifique internet e antivirus.
        pause
        exit /b 1
    )
)
echo.
echo Verificando imports...
"!PYEXE!" -c "import pandas, openpyxl, playwright, httpx, certifi; print('OK: pandas', pandas.__version__, '/ openpyxl', openpyxl.__version__, '/ playwright OK / httpx', httpx.__version__)"
if !errorlevel! neq 0 (
    echo.
    echo [X] Deps instaladas mas import falhou.
    pause
    exit /b 1
)
echo.
echo ============================================================
echo   PRONTO! Agora, sempre que for usar:
echo     2 - ABRIR CHROME.bat   (faca login nos sistemas)
echo     3 - INICIAR AGENTE.bat (deixe aberto)
echo ============================================================
pause
exit /b 0

:detectpy
rem Procura Python REAL em pastas conhecidas. Evita o stub da Store.
set "PYEXE="
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /d %%D in ("C:\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /d %%D in ("%ProgramFiles%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /d %%D in ("%ProgramFiles(x86)%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
rem Ultimo recurso: `python` do PATH — mas so se `--version` responder valido
if not defined PYEXE (
    python --version 2>nul | findstr /R /C:"^Python 3\." >nul 2>&1
    if !errorlevel! equ 0 set "PYEXE=python"
)
exit /b
