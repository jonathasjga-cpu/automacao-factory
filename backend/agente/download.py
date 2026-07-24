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

_STUB_CONFIG_MANAGER = '''"""Stub do config_manager para o agente (attach nao faz login)."""


def get_credencial(*args, **kwargs) -> dict:
    return {"usuario": "", "senha": "", "url": ""}


def salvar_credenciais(*args, **kwargs) -> None:
    return None


def carregar_credenciais(*args, **kwargs) -> dict:
    return {}
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
        "intervalo_poll_seg": 5,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── Raiz do pacote: .bat + LEIA-ME em CRLF ──
        for nome in ("1 - INSTALAR.bat", "2 - ABRIR CHROME.bat", "3 - INICIAR AGENTE.bat"):
            zf.writestr(nome, _crlf(_read(PACOTE_DIR / nome)))
        zf.writestr("LEIA-ME.txt", _crlf(_leia_me(panel_url)))

        # ── Raiz do pacote: config + .py (agente_bot na raiz — layout simplificado) ──
        zf.writestr("agente_config.json", json.dumps(config, indent=2, ensure_ascii=False))
        zf.writestr("agente_bot.py", _read(AGENTE_DIR / "agente_bot.py"))
        zf.writestr("motor_excel.py", _read(AGENTE_DIR / "motor_excel.py"))
        zf.writestr("motor_documentos.py", _read(AGENTE_DIR / "motor_documentos.py"))
        zf.writestr("_tz.py", _read(BACKEND_DIR / "_tz.py"))
        zf.writestr("arquivos_recentes.py", _read(BACKEND_DIR / "arquivos_recentes.py"))
        zf.writestr("browser_config.py", _STUB_BROWSER_CONFIG)
        zf.writestr("config_manager.py", _STUB_CONFIG_MANAGER)

        # ── services/ ──
        zf.writestr("services/__init__.py", "")
        zf.writestr("services/excel_processor.py",
                    _read(SERVICES_DIR / "excel_processor.py"))
        zf.writestr("services/excel_processor_attach.py",
                    _read(SERVICES_DIR / "excel_processor_attach.py"))
        zf.writestr("services/documentos.py",
                    _read(SERVICES_DIR / "documentos.py"))
        zf.writestr("services/documentos_attach.py",
                    _read(SERVICES_DIR / "documentos_attach.py"))

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="AutoFactory-Agente.zip"'},
    )
