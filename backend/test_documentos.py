"""
Script de diagnóstico — roda documentos.py isoladamente sem passar pela automação completa.
Uso: python backend\test_documentos.py
"""
import asyncio
import sys
import os

# Garante que imports funcionem como se fosse o backend
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from services.documentos import executar_salvamento_documentos

# ─── Dados de teste ──────────────────────────────────────────────────────────
# Adapte o número de fatura e factory conforme o que existe no GW hoje
PASTA_TESTE = r"C:\temp\teste_docs"

FATURAS_POR_FACTORY = {
    "firma_matriz": [
        {
            "numero": "5028",          # ← coloque um número de fatura real do GW de hoje
            "valor": 2620.34,
            "cliente_nome": "CLIENTE TESTE",
            "cliente_cnpj": "00.000.000/0001-00",
            "vencimento": "21/04/2026",
            "filial": "MATRIZ",
        }
    ]
}

async def main():
    os.makedirs(PASTA_TESTE, exist_ok=True)
    status = {
        "logs": [],
        "resumo_documentos": {},
    }

    print("=" * 60)
    print("[TESTE] Iniciando teste de salvamento de documentos...")
    print(f"   Pasta: {PASTA_TESTE}")
    print(f"   Faturas: {list(FATURAS_POR_FACTORY.keys())}")
    print("=" * 60)

    try:
        await executar_salvamento_documentos(FATURAS_POR_FACTORY, PASTA_TESTE, status)
    except Exception as e:
        import traceback
        print(f"\n❌ EXCEÇÃO NÃO CAPTURADA: {e}")
        print(traceback.format_exc())

    print("\n" + "=" * 60)
    print("📋 LOGS GERADOS:")
    print("=" * 60)
    for line in status["logs"]:
        print(line)

    print("\n" + "=" * 60)
    print("📊 RESUMO DOCUMENTOS:")
    import json
    print(json.dumps(status.get("resumo_documentos", {}), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
