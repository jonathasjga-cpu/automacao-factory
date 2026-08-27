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

def _verificar_token(
    authorization: Optional[str] = Header(None),
    x_agente_versao: Optional[str] = Header(None),
) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization ausente")
    esperado = f"Bearer {token_mod.get_agente_token()}"
    if authorization != esperado:
        raise HTTPException(status_code=401, detail="Token do agente invalido")
    # Guarda a versao reportada pelo agente
    if x_agente_versao:
        fila.registrar_ping(versao=x_agente_versao)


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
    # Logs detalhados enviados DURANTE a execucao. Antes so chegavam no fim,
    # e uma factory longa parecia travada sem ter feito login.
    logs: Optional[list] = None


@router.post("/progresso/{ordem_id}")
def progresso(ordem_id: str, body: ProgressoBody, _=Depends(_verificar_token)):
    fila.registrar_ping()
    ok = fila.atualizar_progresso(ordem_id, body.feito, body.total, body.desc)
    if not ok:
        raise HTTPException(status_code=404, detail="Ordem nao encontrada")
    # Propaga o `desc` pros logs da operacao amarrada — evita "silencio" na UI
    # enquanto a ordem esta rodando (o resultado so eh injetado no fim).
    try:
        o = fila.get(ordem_id)
        op_id = ((o or {}).get("itens") or {}).get("_operacao_id") or ""
        if op_id and body.desc:
            from operacoes import status_operacoes
            op = status_operacoes.get(op_id)
            if op is not None:
                logs = op.setdefault("logs", [])
                # Logs detalhados do motor primeiro (ordem cronologica)
                for l in (body.logs or []):
                    txt = str(l)[:400]
                    if not logs or logs[-1] != txt:
                        logs.append(txt)
                # Depois a linha de progresso, se mudou
                if body.desc and (not logs or logs[-1] != body.desc):
                    logs.append(body.desc)
    except Exception:
        pass  # nunca deixar isso quebrar o endpoint
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
        # Mescla body.erro no payload: quando o motor morre sem gravar
        # _resultado.json, resultado vem None e o erro fatal ficava SO na fila
        # (que o frontend nao consulta) — a operacao aparecia como 'concluido'
        # com zero arquivos (falso sucesso).
        payload = dict(body.resultado or {})
        if body.erro and not payload.get("erro"):
            payload["erro"] = body.erro
        _injetar_resultado_em_operacao(ordem_id, payload)
    except Exception:
        import traceback
        traceback.print_exc()  # nao propaga; agente ja recebeu OK
    return {"ok": True}


def _injetar_resultado_em_operacao(ordem_id: str, resultado: dict) -> None:
    """Se a ordem estava amarrada a uma operacao_id, mescla resultado no
    status_operacoes. Comportamento por tipo:
      - baixar_documentos: converte arquivos base64 e injeta em arquivos+
        arquivos_recentes; marca concluido. Fluxo de /api/download reusa.
      - executar_factories: agrega logs/erros/concluidas. Se a ordem tinha
        `_encadear_baixar_documentos`, enfileira em seguida uma ordem
        baixar_documentos automaticamente (o fluxo executar+baixar da UI).
    """
    import base64
    o = fila.get(ordem_id)
    if not o:
        return
    itens = o.get("itens") or {}
    op_id = itens.get("_operacao_id") or ""
    if not op_id:
        return
    from operacoes import status_operacoes
    from arquivos_recentes import salvar_pacote
    op = status_operacoes.get(op_id)
    if not op:
        return

    tipo = o.get("tipo", "")
    resultado = resultado or {}

    # Logs acumulam SEM duplicar: a maior parte deles ja subiu durante a
    # execucao via /progresso (logs em tempo real). Adiciona so os que ainda
    # nao estao na cauda recente — senao o painel mostrava tudo em dobro.
    if resultado.get("logs"):
        existentes = op.setdefault("logs", [])
        novos = [str(x) for x in resultado["logs"]]
        janela = set(existentes[-(len(novos) + 80):])
        op["logs"].extend([l for l in novos if l not in janela])
    if resultado.get("erros"):
        op.setdefault("erros", []).extend(resultado["erros"])
    if resultado.get("erro"):
        op.setdefault("erros", []).append(resultado["erro"])

    if tipo == "baixar_documentos":
        arquivos_b64 = resultado.get("arquivos") or {}
        arquivos: dict = {}
        for nome, meta in arquivos_b64.items():
            b64 = (meta or {}).get("b64")
            if not b64:
                continue
            try:
                arquivos[nome] = base64.b64decode(b64)
            except Exception:
                continue
        op.setdefault("arquivos", {}).update(arquivos)
        if resultado.get("resumo_documentos"):
            op["resumo_documentos"] = resultado["resumo_documentos"]
        op["status"] = "concluido" if not op.get("erros") else "concluido_com_erros"
        from datetime import datetime
        op["fim"] = op.get("fim") or datetime.now().isoformat()
        try:
            salvar_pacote(op_id, arquivos, titulo=op.get("titulo") or "Agente", usuario=op.get("usuario") or "")
        except Exception:
            pass
        _persistir_historico(op_id, op, itens)

    elif tipo == "executar_factories":
        # Acumula faturas_salvas e concluidas
        for n in resultado.get("faturas_salvas", []):
            op.setdefault("faturas_salvas", []).append(n)
        op["concluidas"] = op.get("concluidas", 0) + int(resultado.get("concluidas", 0))
        # Erro FATAL da ordem (motor morreu / Chrome nao recuperou) — nao faz
        # sentido encadear o download de documentos: a operacao acabou aqui.
        erro_fatal = o.get("erro") or resultado.get("erro")
        if itens.get("_encadear_baixar_documentos") and not erro_fatal:
            op["status"] = "salvando_documentos"
            op.setdefault("logs", []).append("📥 Factories concluidas — enfileirando baixar documentos...")
            fila.enfileirar(
                tipo="baixar_documentos",
                itens={
                    "faturas_por_factory": itens.get("faturas_por_factory") or op.get("faturas_por_factory") or {},
                    "pasta_destino": op.get("pasta_destino") or "",
                    # Repassa as credenciais da ordem original — sem isso a ordem
                    # encadeada ia sem credencial GW e o auto-login falhava
                    # ("Chrome esta na tela de login do GW e nao consegui logar").
                    "credenciais_por_sistema": itens.get("credenciais_por_sistema") or {},
                    "_operacao_id": op_id,
                },
                usuario=op.get("usuario", ""),
            )
        else:
            from datetime import datetime
            if erro_fatal:
                op.setdefault("logs", []).append(
                    "❌ Factories falharam — download de documentos NAO foi enfileirado."
                )
                op["status"] = "concluido_com_erros"
            else:
                op["status"] = "concluido" if not op.get("erros") else "concluido_com_erros"
            op["fim"] = op.get("fim") or datetime.now().isoformat()
            _persistir_historico(op_id, op, itens)


def _persistir_historico(op_id: str, op: dict, itens: dict) -> None:
    """Salva a operacao no historico. No modo agente, executar_automacao
    retorna logo apos enfileirar — sem isso a operacao nunca aparecia na
    aba 'Relatorios'/'Historico de Operacoes'."""
    try:
        # historico usa faturas_salvas pra calcular qtd/valor; no fluxo
        # 'apenas documentos' ninguem preenche, entao deriva do payload.
        if not op.get("faturas_salvas"):
            numeros = []
            for _sist, lst in (itens.get("faturas_por_factory") or {}).items():
                for f in (lst or []):
                    if isinstance(f, dict) and f.get("numero"):
                        numeros.append(f["numero"])
            if numeros:
                op["faturas_salvas"] = numeros
        from historico_manager import salvar_operacao
        salvar_operacao(op_id, op)
    except Exception:
        pass


# ── Endpoints usados pelo frontend (JWT do usuario) ──────────────────────────

@router.get("/online", dependencies=[Depends(get_current_user)])
def online():
    from .download import get_versao_agente
    versao_agente = fila.versao_reportada()
    versao_atual = get_versao_agente()
    return {
        "online": fila.agente_online(),
        "ultimo_ping": fila.ultimo_ping_iso(),
        "versao_agente": versao_agente,        # o que o agente reportou
        "versao_atual": versao_atual,          # o que o backend tem no zip agora
        "atualizado": bool(versao_agente) and versao_agente == versao_atual,
    }


class EnfileirarFaturasBody(BaseModel):
    data_inicial: Optional[str] = None   # YYYY-MM-DD
    data_final: Optional[str] = None


def _credenciais_gw(current_user) -> dict:
    """Coleta credencial GW: 1o a pessoal do usuario, senao a global de
    Configuracoes. Retorna {} ou {"gw": {...}} pronto pro payload da ordem."""
    try:
        from config_manager import get_credencial, carregar_credenciais
        uid = getattr(current_user, "id", None)
        c = {}
        if uid:
            try:
                c = get_credencial("gw", user_id=uid) or {}
            except Exception:
                c = {}  # usuario sem credencial pessoal — cai pra global
        if not (c.get("usuario") or c.get("senha")):
            c = carregar_credenciais().get("gw", {}) or {}
        if c.get("usuario") or c.get("senha"):
            return {"gw": {
                "usuario": c.get("usuario", ""),
                "senha": c.get("senha", ""),
                "url": c.get("url", ""),
            }}
    except Exception:
        pass
    return {}


@router.post("/enfileirar-faturas")
def enfileirar_faturas(body: EnfileirarFaturasBody, current_user = Depends(get_current_user)):
    """Enfileira uma ordem 'carregar_faturas' pro agente executar.
    Inclui credenciais GW pro agente logar automaticamente se a sessao
    tiver expirado."""
    ordem_id = fila.enfileirar(
        tipo="carregar_faturas",
        itens={
            "data_inicial": body.data_inicial,
            "data_final": body.data_final,
            "credenciais_por_sistema": _credenciais_gw(current_user),
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
        from operacoes import status_operacoes
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
            # Credencial GW pro auto-login se a sessao expirar no meio
            "credenciais_por_sistema": _credenciais_gw(current_user),
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
