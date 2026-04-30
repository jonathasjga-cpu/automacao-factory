"""
Helpers de timezone — força horário de Brasília em todos os "hoje" da aplicação.

Motivo: o Railway roda em UTC. Sem isso, depois das 21h horário de Brasília
o servidor já calcula "hoje" como o dia seguinte, e o filtro de emissão no GW
volta vazio (porque as faturas foram emitidas no dia anterior em horário BR).
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ_BR = ZoneInfo("America/Sao_Paulo")


def now_br() -> datetime:
    """Datetime atual em horário de Brasília."""
    return datetime.now(TZ_BR)


def hoje_br_str(fmt: str = "%d/%m/%Y") -> str:
    """Data de hoje em Brasília no formato dado (default DD/MM/AAAA)."""
    return now_br().strftime(fmt)


def ontem_br_str(fmt: str = "%d/%m/%Y") -> str:
    """Data de ontem em Brasília."""
    return (now_br() - timedelta(days=1)).strftime(fmt)
