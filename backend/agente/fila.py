"""Fila em memoria de ordens do Agente Local.

Comportamento alinhado com status_operacoes: se o backend reinicia, a fila
zera. Isso eh aceitavel — o agente puxa novas ordens quando existirem.
"""
import time
import uuid
from typing import Optional


# Estado global (processo unico — backend precisa rodar com 1 worker)
_ordens: dict = {}          # {ordem_id: dict}

# Ping POR USUARIO: {login: {"ts": float, "versao": str}}
#
# Antes era um dicionario unico do processo inteiro, e isso tinha dois
# efeitos ruins:
#   1. o selo do painel dizia "agente conectado" pra TODO MUNDO sempre que
#      QUALQUER agente estivesse no ar — nao falava da maquina de quem olha;
#   2. proxima_pendente() devolvia a primeira ordem pendente pro primeiro
#      agente que pedisse, ignorando o campo "usuario" que ja era gravado.
#      Com duas pessoas operando, a ordem de uma rodava no Chrome e na
#      sessao GW da outra.
_ultimo_ping: dict = {}

# Agente de pacote antigo, que nao sabe dizer de quem e'. Continua se
# comportando como antes (atende qualquer ordem e deixa todo mundo "online")
# pra nao derrubar quem ja tem o agente instalado. Some sozinho quando a
# pessoa rebaixar o pacote pelo painel ja logada.
LEGADO = ""

LATENCIA_ONLINE_MS = 60_000  # 60s sem ping = agente offline
# Aumentado de 25s pra 60s: Railway tem timeouts intermitentes de 15-30s.
# O agente polla a cada 5s; 60s tolera 2-3 falhas seguidas antes de marcar
# offline, o que evita o badge falso-negativo.


def _agora() -> float:
    return time.time()


def registrar_ping(usuario: str = "", versao: str = "") -> None:
    reg = _ultimo_ping.setdefault(usuario or LEGADO, {"ts": 0.0, "versao": ""})
    reg["ts"] = _agora()
    if versao:
        reg["versao"] = versao


def _vivo(reg: dict | None) -> bool:
    return bool(reg) and (_agora() - reg.get("ts", 0.0)) * 1000 < LATENCIA_ONLINE_MS


def _registro_ativo(usuario: str = "") -> dict | None:
    """O registro que vale pra este usuario: o agente dele, ou um legado.

    Sem usuario (chamada antiga), vale qualquer agente vivo — e' o
    comportamento de antes.
    """
    if usuario:
        candidatos = (usuario, LEGADO)
    else:
        candidatos = tuple(_ultimo_ping.keys())
    vivos = [_ultimo_ping.get(c) for c in candidatos if _vivo(_ultimo_ping.get(c))]
    if not vivos:
        return None
    # O mais recente, pra versao reportada e ultimo_ping baterem com ele
    return max(vivos, key=lambda r: r.get("ts", 0.0))


def versao_reportada(usuario: str = "") -> str:
    reg = _registro_ativo(usuario)
    return (reg or {}).get("versao", "")


def agente_online(usuario: str = "") -> bool:
    return _registro_ativo(usuario) is not None


def ultimo_ping_iso(usuario: str = "") -> str:
    reg = _registro_ativo(usuario)
    ts = (reg or {}).get("ts", 0.0)
    if not ts:
        return ""
    from datetime import datetime
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def enfileirar(tipo: str, itens: dict, usuario: str = "") -> str:
    """Cria nova ordem pendente e retorna seu id."""
    # Sufixo aleatorio: dois enfileiramentos no mesmo milissegundo (threadpool
    # do FastAPI + enqueue encadeado) colidiam e a 2a ordem sobrescrevia a 1a.
    ordem_id = f"ord_{int(_agora() * 1000)}_{uuid.uuid4().hex[:6]}"
    _ordens[ordem_id] = {
        "id": ordem_id,
        "tipo": tipo,
        "itens": itens,
        "usuario": usuario,
        "status": "pendente",          # pendente | executando | concluida | erro
        "criado_em": _agora(),
        "iniciado_em": None,
        "concluido_em": None,
        "resultado": None,
        "erro": None,
        "progresso": {"feito": 0, "total": 0, "desc": "aguardando agente..."},
    }
    return ordem_id


def proxima_pendente(usuario: str = "", legado: bool = False) -> Optional[dict]:
    """Puxa a proxima ordem pendente DESTE usuario (marca 'executando').

    `legado=True` ignora o dono e pega qualquer ordem — e' o que um agente de
    pacote antigo faz, porque ele nao sabe dizer de quem e'. Agente novo so
    pega ordem sua (ou sem dono, que nao deveria existir mas nao custa).
    """
    for o in _ordens.values():
        if o["status"] == "pendente":
            dono = o.get("usuario") or ""
            if not legado and dono and dono != usuario:
                continue
            o["status"] = "executando"
            o["iniciado_em"] = _agora()
            o["progresso"] = {"feito": 0, "total": 0, "desc": "iniciando..."}
            # Copia superficial pra nao expor mutacao acidental
            return dict(o)
    return None


def get(ordem_id: str) -> Optional[dict]:
    return _ordens.get(ordem_id)


def atualizar_progresso(ordem_id: str, feito: int, total: int, desc: str) -> bool:
    o = _ordens.get(ordem_id)
    if not o:
        return False
    o["progresso"] = {"feito": feito, "total": total, "desc": desc}
    return True


def concluir(ordem_id: str, resultado: dict | None, erro: str | None = None) -> bool:
    o = _ordens.get(ordem_id)
    if not o:
        return False
    o["status"] = "erro" if erro else "concluida"
    o["resultado"] = resultado
    o["erro"] = erro
    o["concluido_em"] = _agora()
    return True


def listar() -> list[dict]:
    """Lista todas as ordens (para debug/monitor)."""
    return list(_ordens.values())
