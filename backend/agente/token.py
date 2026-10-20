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
    """Modo agente ligado?

    No RAILWAY o padrao e' LIGADO. O caminho alternativo — Playwright rodando
    no proprio servidor — nao funciona de la: o navegador do container abre a
    tela de login do GW e o formulario nunca aparece. Quando a env var sumiu,
    o painel caiu silenciosamente nesse caminho e o usuario recebeu
    "Timeout 15000ms exceeded waiting for #login", que nao tem relacao
    nenhuma com a causa real.

    Deixar o modo depender de uma env var que pode sumir e' fragil demais pra
    uma coisa que, em producao, so tem um valor correto. Continua sendo
    possivel desligar de proposito com AGENTE_ATIVO=0.

    Fora do Railway (dev local) o padrao segue DESLIGADO: ali o Playwright
    local funciona e e' pratico pra testar sem subir o agente.
    """
    v = os.environ.get("AGENTE_ATIVO", "").strip().lower()
    if v:
        return v in ("1", "true", "yes", "on")
    try:
        from browser_config import IS_RAILWAY
    except Exception:
        IS_RAILWAY = any(os.environ.get(k) for k in (
            "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"))
    return bool(IS_RAILWAY)
