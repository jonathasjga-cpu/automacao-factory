"""Token de autenticacao do Agente Local.

Prioridade:
1. Env var AGENTE_TOKEN (recomendado no Railway).
2. Arquivo persistente ~/.automacao_factory/agente_token
3. Gera novo, persiste em arquivo e retorna.
"""
import os
import secrets
from pathlib import Path

# DATA_DIR, igual a config_manager/db/arquivos_recentes. No Railway ele
# aponta pro volume persistente (/data).
#
# Antes isto era Path.home(), que no container e' filesystem EFEMERO: a cada
# deploy o arquivo sumia, get_agente_token() gerava um token NOVO e TODOS os
# agentes ja baixados passavam a receber 401 no poll — ficavam "offline" pra
# sempre, sem nada na tela do painel explicando. Quem baixasse depois do
# deploy funcionava; quem baixou antes, nao. Era exatamente o sintoma de um
# usuario offline e outro online com o mesmo pacote.
DATA_DIR = Path(os.getenv("DATA_DIR", str(Path.home() / ".automacao_factory")))
TOKEN_FILE = DATA_DIR / "agente_token"

# Compatibilidade: se o token antigo existir no home e o novo caminho ainda
# nao, aproveita o antigo em vez de invalidar os pacotes ja distribuidos.
_TOKEN_FILE_ANTIGO = Path.home() / ".automacao_factory" / "agente_token"


def get_agente_token() -> str:
    env = os.environ.get("AGENTE_TOKEN", "").strip()
    if env:
        return env
    for arquivo in (TOKEN_FILE, _TOKEN_FILE_ANTIGO):
        try:
            if arquivo.exists():
                t = arquivo.read_text(encoding="utf-8").strip()
                if t:
                    if arquivo is not TOKEN_FILE:
                        # migra pro caminho persistente, sem trocar o valor
                        try:
                            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                            TOKEN_FILE.write_text(t, encoding="utf-8")
                        except Exception:
                            pass
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
