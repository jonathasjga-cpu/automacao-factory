"""Gera dinamicamente o .zip do Agente ja configurado.

Layout simplificado (todos os .py na raiz, exceto services/):

  AutoFactory-Agente.zip
  ├── 1 - INSTALAR.bat           (CRLF)
  ├── 2 - ABRIR CHROME.bat       (CRLF)
  ├── 3 - INICIAR AGENTE.bat     (CRLF)
  ├── LEIA-ME.txt                (CRLF)
  ├── agente_config.json         (panel_url + token embutidos)
  ├── agente_bot.py
  ├── motor_excel.py
  ├── _tz.py                     (copiado de backend/)
  ├── browser_config.py          (stub)
  ├── config_manager.py          (stub)
  └── services/
      ├── __init__.py
      ├── excel_processor.py
      └── excel_processor_attach.py

Arquivos de texto (.bat/.txt) sao convertidos LF→CRLF: o servidor roda em
Linux/LF, o Windows precisa CRLF nos .bat pra nao quebrar labels/goto.
"""
import hashlib
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from auth import get_current_user
from . import token as token_mod


router = APIRouter(prefix="/agente", tags=["agente-download"])

AGENTE_DIR = Path(__file__).parent
BACKEND_DIR = AGENTE_DIR.parent
SERVICES_DIR = BACKEND_DIR / "services"
PACOTE_DIR = AGENTE_DIR / "pacote"


def _panel_url_from_request(request: Request) -> str:
    xf_proto = request.headers.get("x-forwarded-proto")
    xf_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if xf_proto and xf_host:
        return f"{xf_proto}://{xf_host}"
    return str(request.base_url).rstrip("/")


_STUB_BROWSER_CONFIG = '''"""Stub do browser_config para o agente (attach nao usa launch)."""
IS_RAILWAY = False


def launch_kwargs(headless: bool = True, extra_args=None) -> dict:
    return {"headless": headless, "channel": "chrome", "args": []}
'''

_STUB_CONFIG_MANAGER = '''"""Config_manager do agente: le credenciais que o backend envia no payload
da ordem. O motor (motor_factories/motor_documentos) grava as credenciais
em _agente_credenciais_atuais.json na raiz do zip.

Se o arquivo nao existir (agente rodando sem ordem), retorna vazio —
comportamento do stub original."""
import json
from pathlib import Path

_CREDS_FILE = Path(__file__).parent / "_agente_credenciais_atuais.json"


def _load() -> dict:
    try:
        if _CREDS_FILE.exists():
            return json.loads(_CREDS_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def get_credencial(sistema: str = "", user_id=None, **kwargs) -> dict:
    return _load().get(sistema, {"usuario": "", "senha": "", "url": ""})


def salvar_credenciais(*args, **kwargs) -> None:
    return None


def carregar_credenciais(*args, **kwargs) -> dict:
    return _load()
'''


def _leia_me(panel_url: str) -> str:
    return f"""AutoFactory - Agente Local
==========================

Painel: {panel_url}

Instale UMA vez. Use SEMPRE:

1) "1 - INSTALAR.bat"     (instala Python + libs; so na primeira vez)
2) "2 - ABRIR CHROME.bat" (perfil isolado; faca login manual no GW)
3) "3 - INICIAR AGENTE.bat" (deixe a janela aberta enquanto usar)

O agente puxa ordens do painel a cada 5s. O Chrome logado continua ativo
enquanto voce nao fechar a janela dele (perfil salvo em cdp_profile/).

Se algo der errado, feche as 3 janelas e comece pelo passo 2 de novo.
"""


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


# ── VERSAO DO AGENTE ────────────────────────────────────────────
# Hash SHA1 dos arquivos que compoem o agente. Se qualquer motor/service
# mudar, o hash muda. Frontend compara com o hash reportado pelo agente
# a cada ping — se diferente, mostra "Agente desatualizado".
_ARQUIVOS_VERSAO = [
    # install.ps1 entra no hash: quando as dependencias mudam (ex: pypdf), o
    # painel precisa avisar pra baixar o pacote novo e rodar '1 - INSTALAR'.
    ("pacote/install.ps1",               PACOTE_DIR / "install.ps1"),
    ("agente_bot.py",                    AGENTE_DIR / "agente_bot.py"),
    ("motor_excel.py",                   AGENTE_DIR / "motor_excel.py"),
    ("motor_documentos.py",              AGENTE_DIR / "motor_documentos.py"),
    ("motor_factories.py",               AGENTE_DIR / "motor_factories.py"),
    ("services/cdp_tabs.py",             SERVICES_DIR / "cdp_tabs.py"),
    ("services/excel_processor.py",      SERVICES_DIR / "excel_processor.py"),
    ("services/excel_processor_attach.py", SERVICES_DIR / "excel_processor_attach.py"),
    ("services/documentos.py",           SERVICES_DIR / "documentos.py"),
    ("services/documentos_attach.py",    SERVICES_DIR / "documentos_attach.py"),
    ("services/firma_automation.py",     SERVICES_DIR / "firma_automation.py"),
    ("services/fluxasset_automation.py", SERVICES_DIR / "fluxasset_automation.py"),
    ("services/gc_automation.py",        SERVICES_DIR / "gc_automation.py"),
    ("services/gc_digitacao.py",         SERVICES_DIR / "gc_digitacao.py"),
    ("services/gc_sifac.py",             SERVICES_DIR / "gc_sifac.py"),
    ("services/factories_attach.py",     SERVICES_DIR / "factories_attach.py"),
]

_versao_cache: dict = {"hash": None}


def get_versao_agente() -> str:
    """Retorna 8 primeiros chars do SHA1 dos arquivos do agente.
    Cache-ado pra nao reler disco a cada request."""
    if _versao_cache["hash"]:
        return _versao_cache["hash"]
    h = hashlib.sha1()
    for nome, path in _ARQUIVOS_VERSAO:
        h.update(nome.encode())
        h.update(b"\0")
        try:
            h.update(path.read_bytes())
        except FileNotFoundError:
            pass
        h.update(b"\0")
    v = h.hexdigest()[:8]
    _versao_cache["hash"] = v
    return v


def _crlf(txt: str) -> bytes:
    """Converte para CRLF (Windows) — Windows precisa disso em .bat/.txt."""
    return txt.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


@router.get("/download", dependencies=[Depends(get_current_user)])
def download(request: Request):
    """Retorna .zip do agente com config embutida."""
    panel_url = _panel_url_from_request(request)
    token = token_mod.get_agente_token()

    config = {
        "panel_url": panel_url,
        "token": token,
        "intervalo_poll_seg": 2,
        "versao": get_versao_agente(),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── Raiz do pacote: .bat + LEIA-ME em CRLF ──
        for nome in ("1 - INSTALAR.bat", "2 - ABRIR CHROME.bat", "3 - INICIAR AGENTE.bat"):
            zf.writestr(nome, _crlf(_read(PACOTE_DIR / nome)))
        # install.ps1 tambem CRLF (Windows PowerShell prefere)
        zf.writestr("install.ps1", _crlf(_read(PACOTE_DIR / "install.ps1")))
        zf.writestr("LEIA-ME.txt", _crlf(_leia_me(panel_url)))

        # ── Raiz do pacote: config + .py (agente_bot na raiz — layout simplificado) ──
        zf.writestr("agente_config.json", json.dumps(config, indent=2, ensure_ascii=False))
        zf.writestr("agente_bot.py", _read(AGENTE_DIR / "agente_bot.py"))
        zf.writestr("motor_excel.py", _read(AGENTE_DIR / "motor_excel.py"))
        zf.writestr("motor_documentos.py", _read(AGENTE_DIR / "motor_documentos.py"))
        zf.writestr("motor_factories.py", _read(AGENTE_DIR / "motor_factories.py"))
        zf.writestr("_tz.py", _read(BACKEND_DIR / "_tz.py"))
        zf.writestr("arquivos_recentes.py", _read(BACKEND_DIR / "arquivos_recentes.py"))
        zf.writestr("browser_config.py", _STUB_BROWSER_CONFIG)
        zf.writestr("config_manager.py", _STUB_CONFIG_MANAGER)

        # ── services/ ──
        zf.writestr("services/__init__.py", "")
        zf.writestr("services/cdp_tabs.py",
                    _read(SERVICES_DIR / "cdp_tabs.py"))
        zf.writestr("services/excel_processor.py",
                    _read(SERVICES_DIR / "excel_processor.py"))
        zf.writestr("services/excel_processor_attach.py",
                    _read(SERVICES_DIR / "excel_processor_attach.py"))
        zf.writestr("services/documentos.py",
                    _read(SERVICES_DIR / "documentos.py"))
        zf.writestr("services/documentos_attach.py",
                    _read(SERVICES_DIR / "documentos_attach.py"))
        # Factories (Firma / FluxAsset / GC) + wrapper attach
        zf.writestr("services/firma_automation.py",
                    _read(SERVICES_DIR / "firma_automation.py"))
        zf.writestr("services/fluxasset_automation.py",
                    _read(SERVICES_DIR / "fluxasset_automation.py"))
        zf.writestr("services/gc_automation.py",
                    _read(SERVICES_DIR / "gc_automation.py"))
        zf.writestr("services/gc_digitacao.py",
                    _read(SERVICES_DIR / "gc_digitacao.py"))
        zf.writestr("services/gc_sifac.py",
                    _read(SERVICES_DIR / "gc_sifac.py"))
        zf.writestr("services/factories_attach.py",
                    _read(SERVICES_DIR / "factories_attach.py"))

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="AutoFactory-Agente.zip"'},
    )
