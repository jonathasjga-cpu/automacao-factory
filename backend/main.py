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

@app.get("/api/credenciais", dependencies=[Depends(get_current_user)])
def get_credenciais():
    creds = carregar_credenciais()
    safe = {}
    for k, v in creds.items():
        safe[k] = {
            "usuario":     v.get("usuario", ""),
            "senha":       v.get("senha", ""),
            "url":         v.get("url", ""),
            "configurado": bool(v.get("senha")),
        }
    return safe

@app.post("/api/credenciais", dependencies=[Depends(get_current_user)])
def post_credenciais(req: CredenciaisRequest):
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

@app.get("/api/faturas", dependencies=[Depends(get_current_user)])
async def get_faturas():
    try:
        from services.excel_processor import processar_dataframes
        faturas = await processar_excels()
        debug = getattr(processar_dataframes, "_last_debug", [])
        return {"faturas": faturas, "debug_complemento": debug}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/faturas-progresso", dependencies=[Depends(get_current_user)])
async def get_faturas_progresso():
    """Progresso do 'Carregar do GW' — frontend usa pra mostrar logs ao vivo."""
    from services.excel_processor import get_progresso_carregar
    return get_progresso_carregar()

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

@app.post("/api/executar", dependencies=[Depends(get_current_user)])
async def executar(req: ExecutarRequest, background_tasks: BackgroundTasks):
    op_id = f"op_{len(status_operacoes)+1}"

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
    op = status_operacoes.get(op_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    arquivos = op.get("arquivos", {})
    if not arquivos:
        raise HTTPException(status_code=404, detail="Nenhum documento disponível ainda")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, dados in arquivos.items():
            if dados:
                zf.writestr(nome, dados)
    buf.seek(0)

    hoje = datetime.now().strftime("%d-%m-%Y")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="documentos-{hoje}.zip"'},
    )

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
