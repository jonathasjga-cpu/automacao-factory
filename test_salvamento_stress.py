"""
Teste de estresse da função baixar_faturas_pdf.

Executa múltiplos cenários sequencialmente para identificar falhas intermitentes.
Cada cenário chama baixar_faturas_pdf com um faturas_por_factory diferente.

Uso:
  python test_salvamento_stress.py                 # todos os cenários
  python test_salvamento_stress.py A B             # só cenários A e B
"""
import asyncio, sys, os, time, json
sys.path.insert(0, "backend")

from services.excel_processor import _cache_faturas
from services.documentos import baixar_faturas_pdf


def pick(filial_kw: str, n: int, excluir=None):
    """Pega N faturas de uma filial (SP ou Matriz)."""
    excluir = excluir or set()
    pool = [
        f for f in _cache_faturas
        if (filial_kw == "SP"  and "SP" in (f.get("filial") or "")) or
           (filial_kw == "MZ"  and "SP" not in (f.get("filial") or ""))
    ]
    pool = [f for f in pool if f["numero"] not in excluir]
    return pool[:n]


def sistema(factory: str) -> str:
    return factory


def mk_status():
    """Dict de status mínimo que baixar_faturas_pdf precisa."""
    return {
        "logs": [],
        "arquivos": {},
        "pasta_destino": "",
        "resumo_documentos": {},
    }


CENARIOS = {
    "A": {
        "desc": "1 factory (GC Matriz), 2 faturas",
        "build": lambda: {"gc_matriz": pick("MZ", 2)},
    },
    "B": {
        "desc": "1 factory (GC SP), 5 faturas",
        "build": lambda: {"gc_sp": pick("SP", 5)},
    },
    "C": {
        "desc": "2 factories mesma filial SP (GC SP + Firma SP)",
        "build": lambda: {
            "gc_sp":    pick("SP", 2),
            "firma_sp": pick("SP", 2, excluir={f["numero"] for f in pick("SP", 2)}),
        },
    },
    "D": {
        "desc": "2 factories filiais diferentes (GC Matriz + GC SP)",
        "build": lambda: {
            "gc_matriz": pick("MZ", 2),
            "gc_sp":     pick("SP", 2),
        },
    },
    "E": {
        "desc": "4 factories, 1 fatura cada",
        "build": lambda: {
            "gc_matriz":    pick("MZ", 1),
            "firma_matriz": pick("MZ", 1, excluir={f["numero"] for f in pick("MZ", 1)}),
            "gc_sp":        pick("SP", 1),
            "firma_sp":     pick("SP", 1, excluir={f["numero"] for f in pick("SP", 1)}),
        },
    },
    "F": {
        "desc": "6 factories cheias (GC + Firma + FluxAsset × Matriz + SP)",
        "build": lambda: {
            "gc_matriz":        pick("MZ", 1),
            "firma_matriz":     pick("MZ", 1, excluir={f["numero"] for f in pick("MZ", 1)}),
            "fluxasset_matriz": pick("MZ", 1, excluir={f["numero"] for f in pick("MZ", 2)}),
            "gc_sp":            pick("SP", 1),
            "firma_sp":         pick("SP", 1, excluir={f["numero"] for f in pick("SP", 1)}),
            "fluxasset_sp":     pick("SP", 1, excluir={f["numero"] for f in pick("SP", 2)}),
        },
    },
}


async def run_cenario(nome: str, cenario: dict) -> dict:
    print(f"\n{'=' * 70}")
    print(f"CENÁRIO {nome}: {cenario['desc']}")
    print('=' * 70)

    fpf = cenario["build"]()
    # Filtra factories vazias
    fpf = {s: lst for s, lst in fpf.items() if lst}
    if not fpf:
        print("  SKIP: sem faturas disponíveis")
        return {"nome": nome, "skip": True}

    for sis, lst in fpf.items():
        nums = [f["numero"] for f in lst]
        print(f"  {sis}: {nums}")

    status = mk_status()
    t0 = time.time()
    try:
        await baixar_faturas_pdf(fpf, status)
        ok = True
        erro = None
    except Exception as e:
        ok = False
        erro = str(e)
    dt = time.time() - t0

    # Conta PDFs salvos
    arquivos = status.get("arquivos", {})
    pdfs = {n: len(b) for n, b in arquivos.items() if n.endswith(".pdf")}

    resumo_docs = status.get("resumo_documentos", {})
    fatura_results = {}
    for sis, info in resumo_docs.items():
        fp = info.get("fatura_pdf") or {}
        fatura_results[sis] = {
            "ok": bool(fp.get("ok") is True or fp.get("path")),
            "motivo": fp.get("motivo", ""),
        }

    # Salva log detalhado
    logdir = os.path.join(os.environ.get("TEMP", "C:/Temp"), "stress_salvamento")
    os.makedirs(logdir, exist_ok=True)
    with open(os.path.join(logdir, f"cenario_{nome}.log"), "w", encoding="utf-8") as f:
        f.write(f"CENÁRIO {nome}: {cenario['desc']}\n")
        f.write(f"Duração: {dt:.1f}s\nOK: {ok}\nErro: {erro}\n")
        f.write(f"PDFs salvos: {pdfs}\n\n")
        f.write("=== Logs da execução ===\n")
        for l in status.get("logs", []):
            f.write(l + "\n")

    print(f"\n  Duração: {dt:.1f}s | Exceção: {erro or 'nenhuma'}")
    print(f"  PDFs gerados: {len(pdfs)}")
    for nome_arq, tam in pdfs.items():
        print(f"    ✓ {nome_arq} ({tam:,} bytes)")
    for sis, r in fatura_results.items():
        status_s = "OK" if r["ok"] else f"FALHA ({r['motivo']})"
        print(f"    [{sis}] {status_s}")

    return {
        "nome": nome,
        "duracao": dt,
        "ok": ok,
        "erro": erro,
        "fatura_results": fatura_results,
        "pdfs_count": len(pdfs),
    }


async def main():
    if not _cache_faturas:
        print("❌ Cache vazia — rode 'Carregar do GW' primeiro para popular.")
        return

    print(f"Faturas na cache: {len(_cache_faturas)}")

    selecionados = sys.argv[1:] if len(sys.argv) > 1 else list(CENARIOS.keys())
    resultados = []
    for nome in selecionados:
        cen = CENARIOS.get(nome)
        if not cen:
            print(f"Cenário desconhecido: {nome}")
            continue
        r = await run_cenario(nome, cen)
        resultados.append(r)
        # pausa entre cenários pra não sobrecarregar
        await asyncio.sleep(2)

    print("\n" + "=" * 70)
    print("RESUMO FINAL")
    print("=" * 70)
    for r in resultados:
        if r.get("skip"):
            continue
        falharam = [s for s, x in r["fatura_results"].items() if not x["ok"]]
        status_s = "✅" if not falharam and r["ok"] else "❌"
        print(f"{status_s} {r['nome']}: {r['pdfs_count']} PDFs | {r['duracao']:.1f}s | falha em: {falharam or 'nenhuma'}")


if __name__ == "__main__":
    asyncio.run(main())
