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
    salvas = status.get("faturas_salvas", set())
    erros = status.get("erros", [])

    # Monta detalhes por factory
    factories = {}
    for num in salvas:
        f = cache.get(num, {})
        factory = f.get("factory_sugerida", "desconhecido")
        if factory not in factories:
            factories[factory] = {"qtd": 0, "valor": 0.0}
        factories[factory]["qtd"] += 1
        factories[factory]["valor"] = round(factories[factory]["valor"] + f.get("valor", 0), 2)

    entrada = {
        "op_id": op_id,
        "data": fim or inicio or datetime.now().isoformat(),
        "inicio": inicio,
        "fim": fim,
        "duracao_seg": duracao_seg,
        "status_final": status.get("status"),
        "total_faturas": status.get("total", 0),
        "concluidas": len(salvas),
        "erros": len(erros),
        "factories": factories,
        "valor_total": round(sum(f["valor"] for f in factories.values()), 2),
    }

    historico.append(entrada)
    HISTORICO_FILE.write_text(
        json.dumps(historico, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return entrada
