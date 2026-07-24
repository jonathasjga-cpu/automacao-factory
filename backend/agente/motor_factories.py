"""Motor Factories — CLI que executa a variante ATTACH das factories.

Recebe faturas_por_factory (dict {sistema: [FaturaSelecao-like]}) e roda
Firma / FluxAsset / GC via CDP. Escreve progresso e resultado.
"""
import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).parent
sys.path.insert(0, str(RAIZ))


def _to_fatura_selecao(d: dict):
    """Recebe dict {numero, factory} e devolve algo com atributos .numero/.factory."""
    return SimpleNamespace(numero=d.get("numero", ""), factory=d.get("factory", ""))


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

    # Grava credenciais recebidas do backend num arquivo temp que o stub
    # config_manager.py do agente le. Assim `fazer_login_*` das factories
    # funciona sem tocar nas originais.
    creds = itens.get("credenciais_por_sistema") or {}
    if creds:
        try:
            (RAIZ / "_agente_credenciais_atuais.json").write_text(
                json.dumps(creds, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # faturas_por_factory: {sistema: [{numero, factory, valor, ...}]}
    faturas_por_factory_raw = itens.get("faturas_por_factory") or {}
    # faturas_cache: {numero: fatura_completa} — o attach precisa disso via status
    faturas_cache = itens.get("faturas_cache") or {}
    # Se nao veio, deriva do dict aninhado
    if not faturas_cache:
        for sist, lst in faturas_por_factory_raw.items():
            for f in lst:
                if isinstance(f, dict) and f.get("numero"):
                    faturas_cache[f["numero"]] = f

    # Converte pra objetos SimpleNamespace com .numero/.factory (como as factories esperam)
    faturas_por_factory_selecao = {}
    for sist, lst in faturas_por_factory_raw.items():
        faturas_por_factory_selecao[sist] = [_to_fatura_selecao({"numero": f.get("numero"), "factory": sist}) for f in lst if isinstance(f, dict)]

    def report(feito: int, total: int, desc: str):
        try:
            prog_file.write_text(
                json.dumps({"feito": feito, "total": total, "desc": desc}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    try:
        report(0, len(faturas_por_factory_selecao), "iniciando factories...")
        status = {
            "op_id": ordem.get("id", ""),
            "logs": [],
            "erros": [],
            "arquivos": {},
            "resumo": [],
            "faturas_cache": faturas_cache,
            "faturas_salvas": set(),
            "concluidas": 0,
            "usuario_id": None,
            "factories": {},
        }
        from services.factories_attach import executar_factories_attach
        asyncio.run(executar_factories_attach(
            faturas_por_factory_selecao=faturas_por_factory_selecao,
            status=status,
            report=report,
        ))
        # Serializa (faturas_salvas eh set — nao vai em JSON)
        status_out = {
            "logs": status.get("logs", [])[-300:],
            "erros": status.get("erros", []),
            "concluidas": status.get("concluidas", 0),
            "faturas_salvas": list(status.get("faturas_salvas", set())),
        }
        res_file.write_text(json.dumps(status_out, ensure_ascii=False), encoding="utf-8")
        report(len(faturas_por_factory_selecao), len(faturas_por_factory_selecao), "factories concluidas")
    except Exception as e:
        tb = traceback.format_exc()
        res_file.write_text(json.dumps({"erro": str(e), "traceback": tb}, ensure_ascii=False), encoding="utf-8")
        report(0, 1, f"erro: {e}")


if __name__ == "__main__":
    main()
