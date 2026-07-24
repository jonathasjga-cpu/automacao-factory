"""Token de autenticacao do Agente Local.

Prioridade:
1. Env var AGENTE_TOKEN (recomendado no Railway).
2. Arquivo persistente ~/.automacao_factory/agente_token
3. Gera novo, persiste em arquivo e retorna.
"""
import os
import secrets
from pathlib import Path

TOKEN_FILE = Path.home() / ".automacao_factory" / "agente_token"


def get_agente_token() -> str:
    env = os.environ.get("AGENTE_TOKEN", "").strip()
    if env:
        return env
    try:
        if TOKEN_FILE.exists():
            t = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if t:
                return t
    except Exception:
        pass
    novo = secrets.token_urlsafe(32)
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(novo, encoding="utf-8")
    except Exception:
        # Em container efemero, pode nao conseguir persistir — token vira
        # "so-esta-execucao". Recomendacao: setar AGENTE_TOKEN no ambiente.
        pass
    return novo


def is_agente_ativo() -> bool:
    return os.environ.get("AGENTE_ATIVO", "").strip().lower() in ("1", "true", "yes", "on")
