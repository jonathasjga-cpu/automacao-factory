import io
import zipfile
import json
import os
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.excel_processor import processar_excels
from services.firma_automation import executar_firma
from services.fluxasset_automation import executar_fluxasset
from services.gc_automation import executar_gc
from services.documentos import executar_salvamento_documentos
from config_manager import salvar_credenciais, carregar_credenciais
from historico_manager import carregar_historico, salvar_operacao
from factory_manager import carregar_factories_extras, salvar_factory_extra, remover_factory_extra, gerar_id
from db import init_db, User
from auth import get_current_user, require_admin
from routers_auth import router as auth_router
from arquivos_recentes import salvar_pacote, listar_pacotes, ler_pacote, limpar_todos
from fastapi import Depends

MSG_FINALIZAR_MANUAL = (
    "Ótimo! Todos os títulos foram digitados com sucesso. "
    "Por questão de segurança, acesse manualmente os sites das factories "
    "para definir a conta corrente e encaminhar a operação."
)

app = FastAPI(title="Automação Factory")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializa banco + admin default
init_db()

# Monta rotas de autenticação e usuários (rotas públicas: /api/auth/login)
app.include_router(auth_router)

status_operacoes: dict = {}

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── Models ────────────────────────────────────────────────────────────────────

class CredenciaisRequest(BaseModel):
    sistema: str
    usuario: str
    senha: str
    url: Optional[str] = None

class FaturaSelecao(BaseModel):
    numero: str
    factory: str

class ExecutarRequest(BaseModel):
    faturas: List[FaturaSelecao]
    pasta_destino: Optional[str] = None
    inicio: Optional[str] = None  # ISO timestamp desde "Carregar do GW"
    apenas_documentos: Optional[bool] = False  # se True, pula factories e só baixa documentos

class DocumentosRequest(BaseModel):
    operacao_id: str

class FactoryExtraRequest(BaseModel):
    nome: str
    icone: Optional[str] = "🏭"
    url: Optional[str] = ""
    usuario: Optional[str] = ""
    senha: Optional[str] = ""

# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/selecionar-pasta", dependencies=[Depends(get_current_user)])
def selecionar_pasta():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        pasta = filedialog.askdirectory(title="Selecionar pasta de destino dos documentos")
        root.destroy()
        if pasta:
            return {"pasta": pasta}
        return {"pasta": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credenciais")
def get_credenciais(current_user = Depends(get_current_user)):
    """
    Retorna credenciais compartilhadas (factories). GW é pessoal (ver /api/meu-gw).
    Usuários comuns não veem senha em texto claro (só usuário + se configurado).
    Admin vê tudo.
    """
    creds = carregar_credenciais()
    is_admin = current_user.role == "admin"
    safe = {}
    for k, v in creds.items():
        if k == "gw":
            continue  # GW agora é por usuário, não exibe aqui
        safe[k] = {
            "usuario":     v.get("usuario", ""),
            "senha":       v.get("senha", "") if is_admin else "",
            "url":         v.get("url", ""),
            "configurado": bool(v.get("senha")),
        }
    return safe

@app.post("/api/credenciais", dependencies=[Depends(require_admin)])
def post_credenciais(req: CredenciaisRequest):
    """Apenas admin edita credenciais compartilhadas. GW pessoal vai em /api/meu-gw."""
    if req.sistema == "gw":
        raise HTTPException(400, detail="GW agora é pessoal. Use /api/meu-gw.")
    salvar_credenciais(req.sistema, req.usuario, req.senha, req.url)
    return {"ok": True}

@app.get("/api/factories-extras", dependencies=[Depends(get_current_user)])
def get_factories_extras():
    return {"factories": carregar_factories_extras()}

@app.post("/api/factories-extras", dependencies=[Depends(get_current_user)])
def post_factory_extra(req: FactoryExtraRequest):
    fid = gerar_id(req.nome)
    salvar_factory_extra({
        "id":      fid,
        "nome":    req.nome,
        "icone":   req.icone or "🏭",
        "url":     req.url or "",
        "usuario": req.usuario or "",
        "senha":   req.senha or "",
        "status":  "pendente",
    })
    return {"ok": True, "id": fid}

@app.delete("/api/factories-extras/{factory_id}", dependencies=[Depends(get_current_user)])
def delete_factory_extra(factory_id: str):
    remover_factory_extra(factory_id)
    return {"ok": True}

@app.get("/api/faturas")
async def get_faturas(current_user = Depends(get_current_user)):
    try:
        from services.excel_processor import processar_dataframes
        faturas = await processar_excels(user_id=current_user.id)
        debug = getattr(processar_dataframes, "_last_debug", [])
        return {"faturas": faturas, "debug_complemento": debug}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/faturas-progresso", dependencies=[Depends(get_current_user)])
async def get_faturas_progresso():
    """Progresso do 'Carregar do GW' — frontend usa pra mostrar logs ao vivo."""
    from services.excel_processor import get_progresso_carregar
    return get_progresso_carregar()

@app.get("/api/faturas-cache", dependencies=[Depends(get_current_user)])
def get_faturas_cache():
    """
    Retorna faturas atualmente em cache — leve, sem disparar nova execução.
    Fallback quando /api/faturas estoura timeout do proxy Railway.
    """
    from services.excel_processor import _cache_faturas, processar_dataframes
    return {"faturas": _cache_faturas, "debug_complemento": getattr(processar_dataframes, "_last_debug", [])}

@app.get("/api/debug-complemento", dependencies=[Depends(get_current_user)])
async def debug_complemento():
    from services.excel_processor import processar_dataframes, _cache_faturas
    debug = getattr(processar_dataframes, "_last_debug", ["sem dados — execute /api/faturas primeiro"])
    com_chave = [f for f in _cache_faturas if f.get("chave")]
    sem_chave = [f["numero"] for f in _cache_faturas if not f.get("chave")]
    return {
        "debug": debug,
        "total_faturas": len(_cache_faturas),
        "com_chave": len(com_chave),
        "sem_chave": sem_chave[:20],
        "exemplo_chave": com_chave[0] if com_chave else None,
    }

@app.post("/api/executar")
async def executar(req: ExecutarRequest, background_tasks: BackgroundTasks,
                   current_user = Depends(get_current_user)):
    op_id = str(len(status_operacoes) + 1)

    from services.excel_processor import _cache_faturas
    faturas_cache = {f["numero"]: f for f in _cache_faturas}

    faturas_por_factory: dict[str, list] = {}
    for f in req.faturas:
        if f.factory == "ignorar":
            continue
        fatura_completa = faturas_cache.get(f.numero)
        if fatura_completa:
            faturas_por_factory.setdefault(f.factory, []).append(fatura_completa)

    status_operacoes[op_id] = {
        "status": "iniciando",
        "total": len([f for f in req.faturas if f.factory != "ignorar"]),
        "concluidas": 0,
        "erros": [],
        "logs": [],
        "resumo": [],
        "faturas_cache": faturas_cache,
        "faturas_por_factory": faturas_por_factory,
        "inicio": req.inicio or datetime.now().isoformat(),
        "fim": None,
        "arquivos": {},
        "pasta_destino": req.pasta_destino or "",
        "usuario": current_user.login,
        "usuario_id": current_user.id,
        "apenas_documentos": bool(req.apenas_documentos),
    }
    background_tasks.add_task(executar_automacao, op_id, req.faturas)
    return {"operacao_id": op_id}

@app.get("/api/status/{op_id}", dependencies=[Depends(get_current_user)])
def get_status(op_id: str):
    if op_id not in status_operacoes:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    s = status_operacoes[op_id]
    # Limpa campos não serializáveis dos sub-status de factories
    factories_clean = {}
    for sistema, fs in s.get("factories", {}).items():
        factories_clean[sistema] = {k: v for k, v in fs.items() if k not in ("faturas_cache", "faturas_salvas")}
    result = {k: v for k, v in s.items() if k not in ("faturas_cache", "arquivos", "factories", "_tasks")}
    # Converte set para lista (set não é JSON-serializável)
    if isinstance(result.get("faturas_salvas"), set):
        result["faturas_salvas"] = list(result["faturas_salvas"])
    result["factories"] = factories_clean
    return result

@app.get("/api/historico", dependencies=[Depends(get_current_user)])
def get_historico():
    return {"historico": carregar_historico()}

@app.post("/api/documentos", dependencies=[Depends(get_current_user)])
async def salvar_documentos(req: DocumentosRequest, background_tasks: BackgroundTasks):
    op = status_operacoes.get(req.operacao_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    faturas_por_factory = op.get("faturas_por_factory", {})
    if not faturas_por_factory:
        raise HTTPException(status_code=400, detail="Nenhuma fatura mapeada por factory")

    op["status"] = "salvando_documentos"
    op["arquivos"] = {}
    background_tasks.add_task(executar_salvamento_documentos, faturas_por_factory, op)
    return {"ok": True}

@app.get("/api/download/{op_id}", dependencies=[Depends(get_current_user)])
def download_documentos(op_id: str):
    # 1) Tenta em memória (operação recém-concluída)
    op = status_operacoes.get(op_id)
    arquivos = op.get("arquivos", {}) if op else None

    # 2) Fallback: lê do disco (arquivos de operações antigas, ainda dentro da retenção)
    if not arquivos:
        arquivos = ler_pacote(op_id)

    if not arquivos:
        raise HTTPException(status_code=404, detail="Nenhum documento disponível ainda")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, dados in arquivos.items():
            if dados:
                zf.writestr(nome, dados)
    buf.seek(0)

    from _tz import now_br
    hoje = now_br().strftime("%d-%m-%Y")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="documentos-{hoje}.zip"'},
    )


@app.get("/api/arquivos-recentes", dependencies=[Depends(get_current_user)])
def listar_arquivos_recentes():
    """Lista pacotes de arquivos gerados nos últimos 2 dias."""
    return {"pacotes": listar_pacotes()}

@app.delete("/api/arquivos-recentes", dependencies=[Depends(require_admin)])
def limpar_arquivos_recentes():
    """Remove todos os pacotes salvos (apenas admin)."""
    n = limpar_todos()
    return {"ok": True, "removidos": n}

@app.post("/api/finalizar/{op_id}", dependencies=[Depends(get_current_user)])
async def finalizar_operacao(op_id: str):
    return {"ok": True, "msg": MSG_FINALIZAR_MANUAL}

@app.post("/api/cancelar/{op_id}", dependencies=[Depends(get_current_user)])
async def cancelar_operacao(op_id: str):
    op = status_operacoes.get(op_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    # Cancela todas as tasks asyncio em andamento
    for t in op.get("_tasks", []):
        if not t.done():
            t.cancel()
    op["status"] = "cancelado"
    op["fim"] = datetime.now().isoformat()
    op["logs"].append("🛑 Operação cancelada pelo usuário")
    return {"ok": True}

# ── Background tasks ──────────────────────────────────────────────────────────

FACTORY_NAMES = {
    "firma_matriz":     "Firma Capital — Matriz",
    "firma_sp":         "Firma Capital — SP",
    "fluxasset_matriz": "FluxAsset — Matriz",
    "fluxasset_sp":     "FluxAsset — SP",
    "gc_matriz":        "GC Recursos — Matriz",
    "gc_sp":            "GC Recursos — SP",
}

async def executar_automacao(op_id: str, faturas: List[FaturaSelecao]):
    status = status_operacoes[op_id]
    status["status"] = "executando"
    status["factories"] = {}

    # Agrupa faturas por factory
    por_factory: dict[str, list] = {}
    for f in faturas:
        if f.factory != "ignorar":
            por_factory.setdefault(f.factory, []).append(f)

    # Cria sub-status por factory
    for sistema, fat_lista in por_factory.items():
        status["factories"][sistema] = {
            "nome":           FACTORY_NAMES.get(sistema, sistema),
            "status":         "aguardando",
            "logs":           [],
            "erros":          [],
            "concluidas":     0,
            "total":          len(fat_lista),
            "faturas_cache":  status["faturas_cache"],
            "faturas_salvas": set(),
        }

    async def _run(sistema: str, fat_lista: list):
        fs = status["factories"][sistema]
        fs["status"] = "executando"
        try:
            if sistema in ("firma_matriz", "firma_sp"):
                await executar_firma(fat_lista, sistema, fs)
            elif sistema in ("fluxasset_matriz", "fluxasset_sp"):
                await executar_fluxasset(fat_lista, sistema, fs)
            elif sistema in ("gc_matriz", "gc_sp"):
                await executar_gc(fat_lista, sistema, fs)
            else:
                raise Exception(f"Factory '{sistema}' nao reconhecida")
            fs["status"] = "concluido" if not fs["erros"] else "erro"
        except asyncio.CancelledError:
            fs["status"] = "cancelado"
            fs["logs"].append("🛑 Cancelado pelo usuário")
        except Exception as e:
            fs["status"] = "erro"
            fs["erros"].append(str(e))
            fs["logs"].append(f"❌ Erro fatal: {str(e)}")

    # Modo "apenas_documentos": pula a digitação nas factories e vai direto pro
    # salvamento de documentos (boletos + CT-es).
    if status.get("apenas_documentos"):
        status["logs"].append("📥 Modo 'Apenas baixar arquivos' — pulando digitação nas factories")
        # Marca todas as factories como concluídas com as faturas selecionadas
        # (necessário pro resumo e pra preservar a relação fatura→factory).
        for sistema, fat_lista in por_factory.items():
            fs = status["factories"][sistema]
            fs["status"] = "concluido"
            fs["concluidas"] = len(fat_lista)
            fs["logs"].append("⏭️ Digitação pulada (modo 'Apenas baixar arquivos')")
            for sel in fat_lista:
                fs["faturas_salvas"].add(sel.numero)
        tasks = []
    else:
        # Executa todas as factories em paralelo — guarda tasks para poder cancelar
        tasks = [asyncio.create_task(_run(sistema, fat_lista)) for sistema, fat_lista in por_factory.items()]
    status["_tasks"] = tasks
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass

    # Se cancelado durante a execução, para aqui
    if status["status"] == "cancelado":
        status["fim"] = status.get("fim") or datetime.now().isoformat()
        salvar_operacao(op_id, status)
        return

    # Agrega no status global — usa lista para manter JSON-serializável
    faturas_salvas_global: list = []
    for fs in status["factories"].values():
        status["concluidas"] += fs["concluidas"]
        status["erros"].extend(fs["erros"])
        faturas_salvas_global.extend(fs.get("faturas_salvas", set()))
    status["faturas_salvas"] = faturas_salvas_global

    # Monta resumo
    status["resumo"] = [
        {
            "factory":  FACTORY_NAMES.get(sistema, sistema),
            "qtd":      fs["concluidas"],
            "qtd_erro": fs["total"] - fs["concluidas"],
            "valor":    round(sum(
                status["faturas_cache"].get(num, {}).get("valor", 0)
                for num in fs.get("faturas_salvas", set())
            ), 2),
        }
        for sistema, fs in status["factories"].items()
    ]

    tem_erros = len(status["erros"]) > 0
    status["logs"].append(f"✅ {MSG_FINALIZAR_MANUAL}")
    if tem_erros:
        status["logs"].append(f"⚠️ {len(status['erros'])} fatura(s) com erro — verifique os cards acima")

    # Salvamento de documentos — agora também cancelável.
    # Registramos como task em _tasks para que /api/cancelar possa interromper.
    if status["faturas_por_factory"] and status["status"] != "cancelado":
        status["status"] = "salvando_documentos"
        status["logs"].append("📥 Baixando boletos e CTes do GW...")
        task_doc = asyncio.create_task(
            executar_salvamento_documentos(status["faturas_por_factory"], status)
        )
        status["_tasks"].append(task_doc)
        try:
            await task_doc
        except asyncio.CancelledError:
            status["logs"].append("🛑 Salvamento de documentos interrompido pelo cancelamento")
        except Exception as e:
            status["logs"].append(f"❌ Erro no salvamento de documentos: {e}")
            status["erros"].append(f"Salvamento documentos: {str(e)}")

    # Se cancelado em qualquer momento (antes ou durante salvamento), não sobrescreve status
    if status["status"] == "cancelado":
        status["fim"] = status.get("fim") or datetime.now().isoformat()
        salvar_operacao(op_id, status)
        return

    status["status"] = "concluido" if not tem_erros else "concluido_com_erros"
    status["fim"] = datetime.now().isoformat()
    salvar_operacao(op_id, status)

    # Persiste arquivos gerados por 2 dias para re-download
    try:
        arquivos = status.get("arquivos") or {}
        if arquivos:
            factories_nomes = [FACTORY_NAMES.get(s, s) for s in status.get("factories", {}).keys()]
            titulo = ", ".join(factories_nomes) or "Operação"
            salvar_pacote(op_id, arquivos, titulo=titulo, usuario=status.get("usuario", ""))
    except Exception as e:
        status["logs"].append(f"⚠️ Falha ao persistir arquivos recentes: {e}")


# ── Serve frontend ────────────────────────────────────────────────────────────

@app.get("/")
def serve_index():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"status": "AutoFactory API running"}

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
