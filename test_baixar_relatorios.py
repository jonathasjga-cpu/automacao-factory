"""Baixa os 2 relatorios do GW e mostra onde foram salvos."""
import asyncio, sys, os
sys.path.insert(0, "backend")
os.environ["PYTHONIOENCODING"] = "utf-8"

from services.excel_processor import baixar_relatorios_gw


async def main():
    print("Baixando 2 relatorios do GW...")
    arquivo1, arquivo2 = await baixar_relatorios_gw(user_id=1)
    print()
    print("=" * 70)
    print(f"AUTOMACAO:  {arquivo1}")
    print(f"COMPLEMENTO: {arquivo2}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
