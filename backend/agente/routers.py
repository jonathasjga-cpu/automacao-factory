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
    # Pos-processamento por tipo — mescla resultado em status_operacoes se
    # a ordem foi amarrada a uma operacao_id no enfileirar.
    try:
        _injetar_resultado_em_operacao(ordem_id, body.resultado or {})
    except Exception:
        import traceback
        traceback.print_exc()  # nao propaga; agente ja recebeu OK
    return {"ok": True}


def _injetar_resultado_em_operacao(ordem_id: str, resultado: dict) -> None:
    """Se a ordem estava amarrada a uma operacao_id (via itens._operacao_id),
    desserializa arquivos base64 e injeta no status_operacoes pra reusar o
    fluxo de /api/download/{op_id} + arquivos_recentes existente."""
    import base64
    o = fila.get(ordem_id)
    if not o:
        return
    op_id = (o.get("itens") or {}).get("_operacao_id") or ""
    if not op_id:
        return
    from main import status_operacoes
    from arquivos_recentes import salvar_pacote
    op = status_operacoes.get(op_id)
    if not op:
        return
    arquivos_b64 = (resultado or {}).get("arquivos") or {}
    arquivos: dict = {}
    for nome, meta in arquivos_b64.items():
        b64 = (meta or {}).get("b64")
        if not b64:
            continue
        try:
            arquivos[nome] = base64.b64decode(b64)
        except Exception:
            continue
    # Mescla no status
    op.setdefault("arquivos", {}).update(arquivos)
    if resultado.get("resumo_documentos"):
        op["resumo_documentos"] = resultado["resumo_documentos"]
    if resultado.get("logs"):
        op.setdefault("logs", []).extend(resultado["logs"])
    # Marca status como concluido pra /api/status responder "pronto"
    op["status"] = "concluido"
    from datetime import datetime
    op["fim"] = op.get("fim") or datetime.now().isoformat()
    # Persiste em disco pra sobreviver 2 dias
    try:
        salvar_pacote(op_id, arquivos, titulo=op.get("titulo") or "Agente", usuario=op.get("usuario") or "")
    except Exception:
        pass


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


class EnfileirarDocumentosBody(BaseModel):
    operacao_id: Optional[str] = None       # se dado, deriva faturas_por_factory + pasta do status_operacoes
    faturas_por_factory: Optional[dict] = None
    pasta_destino: Optional[str] = ""


@router.post("/enfileirar-documentos")
def enfileirar_documentos(body: EnfileirarDocumentosBody, current_user = Depends(get_current_user)):
    """Enfileira 'baixar_documentos' (boletos PDF + CTes PDF/ZIP) pro agente executar.
    Se `operacao_id` for passado, pega faturas_por_factory + pasta_destino do
    status_operacoes existente. Assim frontend nao precisa reenviar tudo, e
    quando o resultado voltar, ja injetamos no status_operacoes[op_id] pra
    reusar o /api/download/{op_id} existente.
    """
    fpf = body.faturas_por_factory
    pasta = body.pasta_destino or ""
    if body.operacao_id and (not fpf):
        # tarde: leitura preguicosa pra evitar import circular
        from main import status_operacoes
        op = status_operacoes.get(body.operacao_id)
        if not op:
            raise HTTPException(status_code=404, detail="operacao_id nao encontrada")
        fpf = op.get("faturas_por_factory") or {}
        pasta = pasta or op.get("pasta_destino") or ""
    if not fpf:
        raise HTTPException(status_code=400, detail="faturas_por_factory vazio")
    ordem_id = fila.enfileirar(
        tipo="baixar_documentos",
        itens={
            "faturas_por_factory": fpf,
            "pasta_destino": pasta,
            # Amarra ordem <-> operacao_id pra injetar arquivos ao concluir
            "_operacao_id": body.operacao_id or "",
        },
        usuario=current_user.login,
    )
    return {"ordem_id": ordem_id, "operacao_id": body.operacao_id}


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
