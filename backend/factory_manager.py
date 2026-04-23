import json
import os
import re
from pathlib import Path

CONFIG_DIR = Path(os.getenv("DATA_DIR", str(Path.home() / ".automacao_factory")))
FACTORIES_FILE = CONFIG_DIR / "factories_extras.json"


def _ler() -> list:
    if not FACTORIES_FILE.exists():
        return []
    try:
        return json.loads(FACTORIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _gravar(lista: list):
    CONFIG_DIR.mkdir(exist_ok=True)
    FACTORIES_FILE.write_text(
        json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def gerar_id(nome: str) -> str:
    """'Minha Factory — SP' → 'minha_factory_sp'"""
    slug = re.sub(r"[^a-z0-9]+", "_", nome.lower().strip())
    return slug.strip("_")[:40]


def carregar_factories_extras() -> list:
    return _ler()


def salvar_factory_extra(factory: dict):
    lista = _ler()
    ids = [f["id"] for f in lista]
    if factory["id"] in ids:
        lista = [factory if f["id"] == factory["id"] else f for f in lista]
    else:
        lista.append(factory)
    _gravar(lista)


def remover_factory_extra(factory_id: str):
    _gravar([f for f in _ler() if f["id"] != factory_id])
