#!/bin/bash
# =====================================================
# AutoFactory — Script de inicialização
# =====================================================

echo ""
echo "⚡ AutoFactory — Iniciando..."
echo ""

# Verifica Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 não encontrado. Instale em https://python.org"
    exit 1
fi

cd "$(dirname "$0")/backend"

# Instala dependências se necessário
if [ ! -d ".venv" ]; then
    echo "📦 Instalando dependências (primeira vez)..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt --quiet
    python3 -m playwright install chromium
    echo "✅ Dependências instaladas!"
else
    source .venv/bin/activate
fi

echo "🌐 Abrindo interface no navegador..."
# Abre a interface
if command -v xdg-open &> /dev/null; then
    xdg-open "../frontend/index.html"
elif command -v open &> /dev/null; then
    open "../frontend/index.html"
fi

echo "🚀 Servidor rodando em http://localhost:8000"
echo "   Pressione Ctrl+C para parar"
echo ""

python3 main.py
