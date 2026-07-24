"""Endpoints da fila do Agente Local.

Dois esquemas de autenticacao:
- Bearer token do agente (endpoints usados pelo agente_bot).
- JWT do usuario (endpoints usados pelo frontend).

Ativado apenas quando AGENTE_ATIVO=1. main.py monta o router condicionalmente.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from auth import get_current_user
from . import fila
from . import token as token_mod


router = APIRouter(prefix="/api/agente", tags=["agente"])


# ── Auth por token do agente ─────────────────────────────────────────────────

def _verificar_token(authorization: Optional[str] = Header(None)) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization ausente")
    esperado = f"Bearer {token_mod.get_agente_token()}"
    if authorization != esperado:
        raise HTTPException(status_code=401, detail="Token do agente invalido")


# ── Endpoints usados pelo agente_bot ─────────────────────────────────────────

@router.get("/proximo")
def proximo(_=Depends(_verificar_token)):
    """Agente puxa a proxima ordem. Cada chamada tambem serve de heartbeat."""
    fila.registrar_ping()
    ordem = fila.proxima_pendente()
    if not ordem:
        return {"tem_ordem": False}
    return {"tem_ordem": True, "ordem": ordem}


@router.post("/ping")
def ping(_=Depends(_verificar_token)):
    fila.registrar_ping()
    return {"ok": True}


class ProgressoBody(BaseModel):
    feito: int = 0
    total: int = 0
    desc: str = ""


@router.post("/progresso/{ordem_id}")
def progresso(ordem_id: str, body: ProgressoBody, _=Depends(_verificar_token)):
    fila.registrar_ping()
    ok = fila.atualizar_progresso(ordem_id, body.feito, body.total, body.desc)
    if not ok:
        raise HTTPException(status_code=404, detail="Ordem nao encontrada")
    return {"ok": True}


class ResultadoBody(BaseModel):
    resultado: Optional[dict] = None
    erro: Optional[str] = None


@router.post("/resultado/{ordem_id}")
def resultado(ordem_id: str, body: ResultadoBody, _=Depends(_verificar_token)):
    fila.registrar_ping()
    ok = fila.concluir(ordem_id, resultado=body.resultado, erro=body.erro)
    if not ok:
        raise HTTPException(status_code=404, detail="Ordem nao encontrada")
    return {"ok": True}


# ── Endpoints usados pelo frontend (JWT do usuario) ──────────────────────────

@router.get("/online", dependencies=[Depends(get_current_user)])
def online():
    return {
        "online": fila.agente_online(),
        "ultimo_ping": fila.ultimo_ping_iso(),
    }


class EnfileirarFaturasBody(BaseModel):
    data_inicial: Optional[str] = None   # YYYY-MM-DD
    data_final: Optional[str] = None


@router.post("/enfileirar-faturas")
def enfileirar_faturas(body: EnfileirarFaturasBody, current_user = Depends(get_current_user)):
    """Enfileira uma ordem 'carregar_faturas' pro agente executar."""
    ordem_id = fila.enfileirar(
        tipo="carregar_faturas",
        itens={
            "data_inicial": body.data_inicial,
            "data_final": body.data_final,
        },
        usuario=current_user.login,
    )
    return {"ordem_id": ordem_id}


@router.get("/ordem/{ordem_id}", dependencies=[Depends(get_current_user)])
def ver_ordem(ordem_id: str):
    o = fila.get(ordem_id)
    if not o:
        raise HTTPException(status_code=404, detail="Ordem nao encontrada")
    # Nao vaza campos sensiveis
    return {
        "id": o["id"],
        "tipo": o["tipo"],
        "status": o["status"],
        "criado_em": o["criado_em"],
        "iniciado_em": o["iniciado_em"],
        "concluido_em": o["concluido_em"],
        "progresso": o["progresso"],
        "resultado": o.get("resultado"),
        "erro": o.get("erro"),
    }


@router.get("/ordens", dependencies=[Depends(get_current_user)])
def listar_ordens():
    """Lista todas as ordens (debug/monitor)."""
    return {"ordens": [
        {
            "id": o["id"], "tipo": o["tipo"], "status": o["status"],
            "criado_em": o["criado_em"], "concluido_em": o["concluido_em"],
            "progresso": o["progresso"],
        }
        for o in fila.listar()
    ]}
