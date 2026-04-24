"""
Chama baixar_faturas_pdf() diretamente com dados de producao real.
Simula exatamente o que o servidor faz, sem o FastAPI.

Executa: .venv\Scripts\python.exe test_fatura_producao.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from services.documentos import baixar_faturas_pdf


async def main():
    status = {
        "logs": [],
        "resumo_documentos": {},
        "arquivos": {},
        "pasta_destino": "C:/Temp",
    }

    # Dados iguais aos que chegam em producao
    faturas_por_factory = {
        "fluxasset_matriz": [
            {
                "numero": "005148",
                "emissao": "21/04/2026",
                "vencimento": "21/05/2026",
                "valor": 1654.50,
                "cliente_nome": "DOCILE NORDESTE",
                "chave": "",
            },
            {
                "numero": "005149",
                "emissao": "21/04/2026",
                "vencimento": "21/05/2026",
                "valor": 3154.43,
                "cliente_nome": "FARMABASE",
                "chave": "",
            },
        ],
    }

    print("=" * 60)
    print("Chamando baixar_faturas_pdf() diretamente...")
    print("=" * 60)

    await baixar_faturas_pdf(faturas_por_factory, status)

    print("\n=== LOGS COMPLETOS ===")
    for linha in status["logs"]:
        print(f"  {linha}")

    arqs = list(status.get("arquivos", {}).keys())
    print(f"\n=== ARQUIVOS GERADOS: {arqs} ===")

    input("\nPressione Enter para fechar...")


asyncio.run(main())
