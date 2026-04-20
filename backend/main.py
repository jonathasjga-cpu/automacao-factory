import io
import zipfile
import json
import os
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

status_operacoes: dict = {}

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── Models ────────────────────────────────────────────────────────────────────

class CredenciaisRequest(BaseModel):
    sistema: str
    usuario: str
    senha: str

class FaturaSelecao(BaseModel):
    numero: str
    factory: str

class ExecutarRequest(BaseModel):
    faturas: List[FaturaSelecao]

class DocumentosRequest(BaseModel):
    operacao_id: str

# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/credenciais")
def get_credenciais():
    creds = carregar_credenciais()
    safe = {}
    for k, v in creds.items():
        safe[k] = {"usuario": v.get("usuario", ""), "configurado": bool(v.get("senha"))}
    return safe

@app.post("/api/credenciais")
def post_credenciais(req: CredenciaisRequest):
    salvar_credenciais(req.sistema, req.usuario, req.senha)
    return {"ok": True}

@app.get("/api/faturas")
async def get_faturas():
    try:
        faturas = await processar_excels()
        return {"faturas": faturas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/executar")
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
        "inicio": datetime.now().isoformat(),
        "fim": None,
        "arquivos": {},
    }
    background_tasks.add_task(executar_automacao, op_id, req.faturas)
    return {"operacao_id": op_id}

@app.get("/api/status/{op_id}")
def get_status(op_id: str):
    if op_id not in status_operacoes:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    s = status_operacoes[op_id]
    return {k: v for k, v in s.items() if k not in ("faturas_cache", "arquivos")}

@app.get("/api/historico")
def get_historico():
    return {"historico": carregar_historico()}

@app.post("/api/documentos")
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

@app.get("/api/download/{op_id}")
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

@app.post("/api/finalizar/{op_id}")
async def finalizar_operacao(op_id: str):
    return {"ok": True, "msg": MSG_FINALIZAR_MANUAL}

# ── Background tasks ──────────────────────────────────────────────────────────

async def executar_automacao(op_id: str, faturas: List[FaturaSelecao]):
    status = status_operacoes[op_id]
    status["status"] = "executando"

    firma_matriz     = [f for f in faturas if f.factory == "firma_matriz"]
    firma_sp         = [f for f in faturas if f.factory == "firma_sp"]
    fluxasset_matriz = [f for f in faturas if f.factory == "fluxasset_matriz"]
    fluxasset_sp     = [f for f in faturas if f.factory == "fluxasset_sp"]
    gc_matriz        = [f for f in faturas if f.factory == "gc_matriz"]
    gc_sp            = [f for f in faturas if f.factory == "gc_sp"]

    resumo = []

    try:
        if firma_matriz:
            status["logs"].append("🏢 Iniciando operação FIRMA - Matriz...")
            await executar_firma(firma_matriz, "firma_matriz", status)
            resumo.append(_montar_resumo("Firma Capital — Matriz", firma_matriz, status))
            status["resumo"] = resumo

        if firma_sp:
            status["logs"].append("🏙️ Iniciando operação FIRMA - Filial SP...")
            await executar_firma(firma_sp, "firma_sp", status)
            resumo.append(_montar_resumo("Firma Capital — SP", firma_sp, status))
            status["resumo"] = resumo

        if fluxasset_matriz:
            status["logs"].append("🏢 Iniciando operação FLUXASSET - Matriz...")
            await executar_fluxasset(fluxasset_matriz, "fluxasset_matriz", status)
            resumo.append(_montar_resumo("FluxAsset — Matriz", fluxasset_matriz, status))
            status["resumo"] = resumo

        if fluxasset_sp:
            status["logs"].append("🏙️ Iniciando operação FLUXASSET - Filial SP...")
            await executar_fluxasset(fluxasset_sp, "fluxasset_sp", status)
            resumo.append(_montar_resumo("FluxAsset — SP", fluxasset_sp, status))
            status["resumo"] = resumo

        if gc_matriz:
            status["logs"].append("🏢 Iniciando operação GC - Matriz...")
            await executar_gc(gc_matriz, "gc_matriz", status)
            resumo.append(_montar_resumo("GC Recursos — Matriz", gc_matriz, status))
            status["resumo"] = resumo

        if gc_sp:
            status["logs"].append("🏙️ Iniciando operação GC - Filial SP...")
            await executar_gc(gc_sp, "gc_sp", status)
            resumo.append(_montar_resumo("GC Recursos — SP", gc_sp, status))
            status["resumo"] = resumo

        status["resumo"] = resumo
        status["logs"].append(f"✅ {MSG_FINALIZAR_MANUAL}")

        # Baixar documentos automaticamente após digitação
        if status["faturas_por_factory"]:
            status["status"] = "salvando_documentos"
            status["logs"].append("📥 Baixando boletos e CTes do GW...")
            await executar_salvamento_documentos(status["faturas_por_factory"], status)

        status["status"] = "concluido"
        status["fim"] = datetime.now().isoformat()
        salvar_operacao(op_id, status)

    except Exception as e:
        status["status"] = "erro"
        status["fim"] = datetime.now().isoformat()
        status["erros"].append(str(e))
        status["logs"].append(f"❌ Erro: {str(e)}")
        salvar_operacao(op_id, status)


def _montar_resumo(nome: str, faturas_selecao, status: dict) -> dict:
    cache = status.get("faturas_cache", {})
    salvas = status.get("faturas_salvas", set())
    total = len(faturas_selecao)
    ok = [f for f in faturas_selecao if f.numero in salvas]
    qtd_erro = total - len(ok)
    valor = sum(cache[f.numero]["valor"] for f in ok if f.numero in cache)
    return {
        "factory": nome,
        "qtd": len(ok),
        "qtd_erro": qtd_erro,
        "valor": round(valor, 2),
    }


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
