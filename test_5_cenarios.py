"""
Roda 5 cenários de salvamento de fatura PDF + CT-es contra o GW real.
Sem alterar código — só consome documentos.baixar_faturas_pdf e baixar_ctes_pdf.
"""
import asyncio, sys, os, time, json
sys.path.insert(0, "backend")
os.environ["PYTHONIOENCODING"] = "utf-8"

from datetime import datetime
from services import excel_processor   # acessa _cache_faturas dinamicamente
from services.excel_processor import processar_excels
from services.documentos import baixar_faturas_pdf, baixar_ctes_pdf


def cache():
    return excel_processor._cache_faturas


def pick(filial_kw: str, n: int, excluir=None):
    excluir = excluir or set()
    pool = [
        f for f in cache()
        if (filial_kw == "SP" and "SP" in (f.get("filial") or "")) or
           (filial_kw == "MZ" and "SP" not in (f.get("filial") or ""))
    ]
    pool = [f for f in pool if f["numero"] not in excluir]
    return pool[:n]


def mk_status():
    return {
        "logs": [],
        "arquivos": {},
        "pasta_destino": "",
        "resumo_documentos": {},
        "usuario_id": 1,  # admin
    }


def montar_cenarios():
    """Constrói cenários a partir das faturas que vieram do GW."""
    fats = cache()
    sp = [f for f in fats if "SP" in (f.get("filial") or "")]
    mz = [f for f in fats if "SP" not in (f.get("filial") or "")]
    cenarios = {}
    if mz:
        cenarios["A"] = {
            "desc": f"1 factory MATRIZ, {min(2, len(mz))} fatura(s)",
            "fpf": {"gc_matriz": mz[:2]},
        }
    if sp:
        cenarios["B"] = {
            "desc": f"1 factory SP, {min(3, len(sp))} fatura(s)",
            "fpf": {"firma_sp": sp[:3]},
        }
    if len(sp) >= 2:
        cenarios["C"] = {
            "desc": "2 factories mesma filial SP (GC SP + Firma SP)",
            "fpf": {
                "gc_sp": sp[:1],
                "firma_sp": sp[1:2],
            },
        }
    if mz and sp:
        cenarios["D"] = {
            "desc": "2 factories filiais diferentes",
            "fpf": {
                "gc_matriz": mz[:1],
                "gc_sp":     sp[:1],
            },
        }
    if mz and sp and len(mz) >= 2 and len(sp) >= 2:
        cenarios["E"] = {
            "desc": "4 factories — 1 fatura cada",
            "fpf": {
                "gc_matriz":    mz[:1],
                "firma_matriz": mz[1:2],
                "gc_sp":        sp[:1],
                "firma_sp":     sp[1:2],
            },
        }
    return cenarios


async def run_cenario(nome: str, cen: dict):
    print(f"\n{'=' * 70}")
    print(f"CENÁRIO {nome}: {cen['desc']}")
    print('=' * 70)

    fpf = {s: lst for s, lst in cen["fpf"].items() if lst}
    for s, lst in fpf.items():
        print(f"  {s}: {[f['numero'] for f in lst]}")

    status = mk_status()

    t0 = time.time()
    try:
        await baixar_faturas_pdf(fpf, status)
        await baixar_ctes_pdf(fpf, status)
        ok = True
        erro = None
    except Exception as e:
        ok = False
        erro = str(e)
    dt = time.time() - t0

    arquivos = status.get("arquivos", {})
    pdfs = sum(1 for n in arquivos if n.endswith(".pdf"))
    zips = sum(1 for n in arquivos if n.endswith(".zip"))

    rd = status.get("resumo_documentos", {})
    fat_ok = sum(1 for x in rd.values() if (x.get("fatura_pdf") or {}).get("ok"))
    fat_fail = sum(1 for x in rd.values() if (x.get("fatura_pdf") and not x["fatura_pdf"].get("ok")))
    cte_ok = sum(sum(1 for c in x.get("ctes", []) if c.get("ok")) for x in rd.values())
    cte_fail = sum(sum(1 for c in x.get("ctes", []) if not c.get("ok")) for x in rd.values())

    print(f"\n  Duração: {dt:.1f}s | exception: {erro or 'nenhuma'}")
    print(f"  PDFs salvos: {pdfs} | ZIPs: {zips}")
    print(f"  Fatura PDF: {fat_ok} OK / {fat_fail} falha")
    print(f"  CT-es: {cte_ok} OK / {cte_fail} falha")

    return {
        "nome": nome,
        "duracao": dt,
        "ok": ok and not erro and fat_fail == 0 and cte_fail == 0,
        "fat_ok": fat_ok, "fat_fail": fat_fail,
        "cte_ok": cte_ok, "cte_fail": cte_fail,
        "erro": erro,
    }


async def main():
    print("Refrescando cache do GW (hoje)...")
    try:
        await processar_excels(user_id=1)
    except Exception as e:
        print(f"  AVISO: refresh falhou: {e}")

    fats = cache()
    print(f"Cache atual: {len(fats)} faturas")
    sp = sum(1 for f in fats if "SP" in (f.get("filial") or ""))
    mz = len(fats) - sp
    print(f"  SP: {sp} | Matriz: {mz}")

    if not fats:
        print("Sem faturas — abortando")
        return

    cenarios = montar_cenarios()
    if not cenarios:
        print("Não foi possível montar cenários (poucas faturas)")
        return

    resultados = []
    for nome, cen in cenarios.items():
        r = await run_cenario(nome, cen)
        resultados.append(r)
        await asyncio.sleep(2)

    print("\n" + "=" * 70)
    print("RESUMO FINAL")
    print("=" * 70)
    for r in resultados:
        marca = "OK" if r["ok"] else "FALHA"
        print(f"  [{marca}] {r['nome']}: {r['duracao']:.1f}s | "
              f"fat={r['fat_ok']}/{r['fat_ok']+r['fat_fail']} "
              f"cte={r['cte_ok']}/{r['cte_ok']+r['cte_fail']}"
              + (f" | exc: {r['erro']}" if r['erro'] else ''))


if __name__ == "__main__":
    asyncio.run(main())
