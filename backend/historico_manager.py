"""Gerencia o histórico persistente de operações concluídas"""
import json
import os
from pathlib import Path
from datetime import datetime

_DATA_DIR = Path(os.getenv("DATA_DIR", str(Path.home() / ".automacao_factory")))
HISTORICO_FILE = _DATA_DIR / "operacoes_historico.json"

def carregar_historico() -> list:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not HISTORICO_FILE.exists():
        return []
    try:
        return json.loads(HISTORICO_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def salvar_operacao(op_id: str, status: dict):
    """Persiste uma operação concluída no histórico"""
    historico = carregar_historico()

    inicio = status.get("inicio")
    fim = status.get("fim")
    duracao_seg = None
    if inicio and fim:
        try:
            t0 = datetime.fromisoformat(inicio)
            t1 = datetime.fromisoformat(fim)
            duracao_seg = round((t1 - t0).total_seconds())
        except Exception:
            pass

    cache = status.get("faturas_cache", {})
    erros = status.get("erros", [])

    # Monta factories a partir do sub-status por factory (correto para FluxAsset/GC/Firma)
    factories: dict = {}
    titulos: list = []
    for sistema, fs in status.get("factories", {}).items():
        fat_salvas = fs.get("faturas_salvas", set())
        if not fat_salvas:
            continue
        qtd   = len(fat_salvas)
        valor = round(sum(cache.get(num, {}).get("valor", 0) for num in fat_salvas), 2)
        factories[sistema] = {"qtd": qtd, "valor": valor}
        for num in fat_salvas:
            f = cache.get(num, {})
            titulos.append({
                "numero":  num,
                "cliente": f.get("cliente_nome", ""),
                "valor":   f.get("valor", 0),
                "factory": sistema,
                "filial":  f.get("filial", ""),
            })

    # Fallback: se factories não tem dados (path antigo), usa faturas_salvas + factory_sugerida
    if not factories:
        salvas = status.get("faturas_salvas", set())
        for num in salvas:
            f = cache.get(num, {})
            factory = f.get("factory_sugerida", "desconhecido")
            if factory not in factories:
                factories[factory] = {"qtd": 0, "valor": 0.0}
            factories[factory]["qtd"] += 1
            factories[factory]["valor"] = round(factories[factory]["valor"] + f.get("valor", 0), 2)
            titulos.append({
                "numero":  num,
                "cliente": f.get("cliente_nome", ""),
                "valor":   f.get("valor", 0),
                "factory": factory,
                "filial":  f.get("filial", ""),
            })

    titulos.sort(key=lambda x: x["numero"])

    entrada = {
        "op_id": op_id,
        "data": fim or inicio or datetime.now().isoformat(),
        "inicio": inicio,
        "fim": fim,
        "duracao_seg": duracao_seg,
        "status_final": status.get("status"),
        "total_faturas": status.get("total", 0),
        "concluidas": sum(f["qtd"] for f in factories.values()),
        "erros": len(erros),
        "factories": factories,
        "valor_total": round(sum(f["valor"] for f in factories.values()), 2),
        "titulos": titulos,
    }

    historico.append(entrada)
    HISTORICO_FILE.write_text(
        json.dumps(historico, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return entrada
