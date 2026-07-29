"""Motor Excel — CLI que executa a variante ATTACH do excel_processor.

Escreve _progresso.json continuamente e _resultado.json ao final.
Uso: python motor_excel.py --job job.json --progresso p.json --resultado r.json
"""
import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

# O agente rodara este script com cwd = pasta do agente. Garantimos que
# o import de services.excel_processor_attach funciona:
RAIZ = Path(__file__).parent
sys.path.insert(0, str(RAIZ))


def _iso_para_br(iso: str | None) -> str | None:
    if not iso:
        return None
    s = str(iso)[:10]
    parts = s.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--progresso", required=True)
    ap.add_argument("--resultado", required=True)
    args = ap.parse_args()

    job_file = Path(args.job)
    prog_file = Path(args.progresso)
    res_file = Path(args.resultado)

    ordem = json.loads(job_file.read_text(encoding="utf-8"))
    itens = ordem.get("itens") or {}
    data_ini_br = _iso_para_br(itens.get("data_inicial"))
    data_fim_br = _iso_para_br(itens.get("data_final"))

    # Grava credenciais recebidas do backend num arquivo temp que o stub
    # config_manager.py do agente le. Necessario pro auto-login do GW quando
    # a sessao expira (403).
    # SEMPRE grava (mesmo vazio) — ver comentario em motor_factories.py
    creds = itens.get("credenciais_por_sistema") or {}
    try:
        (RAIZ / "_agente_credenciais_atuais.json").write_text(
            json.dumps(creds, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

    def report(feito: int, total: int, desc: str):
        try:
            prog_file.write_text(
                json.dumps({"feito": feito, "total": total, "desc": desc}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    try:
        report(0, 3, "conectando ao Chrome (CDP :9222)...")
        from services.excel_processor_attach import carregar_faturas_attach
        faturas = asyncio.run(carregar_faturas_attach(
            data_inicial_br=data_ini_br,
            data_final_br=data_fim_br,
            report=report,
        ))
        res_file.write_text(
            json.dumps({"faturas": faturas}, ensure_ascii=False),
            encoding="utf-8",
        )
        report(3, 3, f"concluido: {len(faturas)} fatura(s)")
    except Exception as e:
        tb = traceback.format_exc()
        res_file.write_text(
            json.dumps({"erro": str(e), "traceback": tb}, ensure_ascii=False),
            encoding="utf-8",
        )
        report(0, 1, f"erro: {e}")


if __name__ == "__main__":
    main()
