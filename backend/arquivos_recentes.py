"""
Armazenamento persistente de arquivos gerados por operações.
Retenção: 2 dias. Após isso, limpa automaticamente.
"""
import os
import json
import shutil
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path.home() / ".automacao_factory")))
ROOT = DATA_DIR / "arquivos_recentes"
ROOT.mkdir(parents=True, exist_ok=True)

RETENCAO_DIAS = 2


def _meta_path(op_id: str) -> Path:
    return ROOT / op_id / "_meta.json"


def salvar_pacote(op_id: str, arquivos: dict[str, bytes], titulo: str = "") -> None:
    """Salva os arquivos de uma operação em disco."""
    if not arquivos:
        return
    pasta = ROOT / op_id
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, dados in arquivos.items():
        if dados:
            (pasta / nome).write_bytes(dados)
    meta = {
        "op_id": op_id,
        "titulo": titulo or op_id,
        "criado_em": datetime.now().isoformat(),
        "arquivos": [
            {"nome": n, "tamanho": len(b or b"")}
            for n, b in arquivos.items()
            if b
        ],
    }
    _meta_path(op_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def listar_pacotes() -> list[dict]:
    """Lista pacotes disponíveis (ordenados por mais recente), já fazendo cleanup."""
    _limpar_antigos()
    out = []
    for sub in sorted(ROOT.iterdir(), reverse=True):
        if not sub.is_dir():
            continue
        meta_f = sub / "_meta.json"
        if not meta_f.exists():
            continue
        try:
            out.append(json.loads(meta_f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def ler_pacote(op_id: str) -> dict[str, bytes] | None:
    """Retorna os arquivos de uma operação persistida, ou None se não existir."""
    pasta = ROOT / op_id
    if not pasta.exists() or not _meta_path(op_id).exists():
        return None
    arquivos: dict[str, bytes] = {}
    for item in pasta.iterdir():
        if item.is_file() and item.name != "_meta.json":
            arquivos[item.name] = item.read_bytes()
    return arquivos


def _limpar_antigos():
    """Remove pacotes criados há mais de RETENCAO_DIAS dias."""
    limite = datetime.now() - timedelta(days=RETENCAO_DIAS)
    for sub in ROOT.iterdir():
        if not sub.is_dir():
            continue
        meta_f = sub / "_meta.json"
        try:
            if meta_f.exists():
                meta = json.loads(meta_f.read_text(encoding="utf-8"))
                dt = datetime.fromisoformat(meta["criado_em"])
            else:
                dt = datetime.fromtimestamp(sub.stat().st_mtime)
            if dt < limite:
                shutil.rmtree(sub, ignore_errors=True)
        except Exception:
            continue
