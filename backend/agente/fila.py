"""Fila em memoria de ordens do Agente Local.

Comportamento alinhado com status_operacoes: se o backend reinicia, a fila
zera. Isso eh aceitavel — o agente puxa novas ordens quando existirem.
"""
import time
from typing import Optional


# Estado global (processo unico — backend precisa rodar com 1 worker)
_ordens: dict = {}          # {ordem_id: dict}
_ultimo_ping: dict = {"ts": 0.0}
LATENCIA_ONLINE_MS = 60_000  # 60s sem ping = agente offline
# Aumentado de 25s pra 60s: Railway tem timeouts intermitentes de 15-30s.
# O agente polla a cada 5s; 60s tolera 2-3 falhas seguidas antes de marcar
# offline, o que evita o badge falso-negativo.


def _agora() -> float:
    return time.time()


def registrar_ping() -> None:
    _ultimo_ping["ts"] = _agora()


def agente_online() -> bool:
    return (_agora() - _ultimo_ping["ts"]) * 1000 < LATENCIA_ONLINE_MS


def ultimo_ping_iso() -> str:
    ts = _ultimo_ping["ts"]
    if not ts:
        return ""
    from datetime import datetime
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def enfileirar(tipo: str, itens: dict, usuario: str = "") -> str:
    """Cria nova ordem pendente e retorna seu id."""
    ordem_id = f"ord_{int(_agora() * 1000)}"
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


def proxima_pendente() -> Optional[dict]:
    """Puxa a proxima ordem pendente (marca como 'executando') ou retorna None."""
    for o in _ordens.values():
        if o["status"] == "pendente":
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
