"""Motor Documentos — CLI que executa a variante ATTACH do documentos.py.

Escreve _progresso.json continuamente e _resultado.json ao final.
Uso: python motor_documentos.py --job job.json --progresso p.json --resultado r.json
"""
import argparse
import asyncio
import base64
import json
import sys
import traceback
from pathlib import Path

RAIZ = Path(__file__).parent
sys.path.insert(0, str(RAIZ))


def _serializar_arquivos(arquivos: dict) -> dict:
    """Arquivos vem como bytes. Serializa como base64 pra passar por JSON."""
    out = {}
    for nome, dados in (arquivos or {}).items():
        if isinstance(dados, (bytes, bytearray)):
            out[nome] = {"b64": base64.b64encode(bytes(dados)).decode("ascii"), "size": len(dados)}
        else:
            out[nome] = {"nao_serializavel": True, "type": type(dados).__name__}
    return out


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
    faturas_por_factory = itens.get("faturas_por_factory") or {}
    pasta_destino = itens.get("pasta_destino") or ""

    def report(feito: int, total: int, desc: str):
        try:
            prog_file.write_text(
                json.dumps({"feito": feito, "total": total, "desc": desc}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    try:
        report(0, 4, "iniciando motor de documentos...")
        # Simula o `status` que o backend original usa
        status: dict = {
            "op_id": ordem.get("id", ""),
            "logs": [],
            "arquivos": {},
            "pasta_destino": pasta_destino,
            "resumo_documentos": {},
            # usuario_id nao eh usado pelo attach (nao ha login), mas mantem chave
            "usuario_id": None,
        }
        from services.documentos_attach import baixar_documentos_attach
        asyncio.run(baixar_documentos_attach(
            faturas_por_factory=faturas_por_factory,
            status=status,
            report=report,
        ))
        # Serializa arquivos pra JSON (base64)
        arquivos_b64 = _serializar_arquivos(status.get("arquivos"))
        resumo = status.get("resumo_documentos", {})
        # Sanitiza logs a esse tamanho (evita _resultado.json gigante)
        logs = status.get("logs", [])[-200:]

        res_file.write_text(json.dumps({
            "arquivos": arquivos_b64,
            "resumo_documentos": resumo,
            "logs": logs,
        }, ensure_ascii=False), encoding="utf-8")
        report(4, 4, f"concluido: {len(arquivos_b64)} arquivo(s)")
    except Exception as e:
        tb = traceback.format_exc()
        res_file.write_text(json.dumps({
            "erro": str(e),
            "traceback": tb,
        }, ensure_ascii=False), encoding="utf-8")
        report(0, 1, f"erro: {e}")


if __name__ == "__main__":
    main()
