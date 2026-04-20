@echo off
echo.
echo ⚡ AutoFactory — Iniciando...
echo.

cd /d "%~dp0backend"

if not exist ".venv" (
    echo 📦 Instalando dependências (primeira vez)...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt --quiet
    python -m playwright install chromium
    echo ✅ Dependências instaladas!
) else (
    call .venv\Scripts\activate.bat
)

echo 🌐 Abrindo interface no navegador...
start "" "..\frontend\index.html"

echo 🚀 Servidor rodando em http://localhost:8000
echo    Pressione Ctrl+C para parar
echo.

python main.py
pause
