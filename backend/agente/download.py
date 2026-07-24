"""Gera dinamicamente o .zip do Agente ja configurado.

Inclui: agente_bot.py + motor_excel.py + services (excel_processor +
excel_processor_attach) + stubs de deps + config JSON com panel_url e token
ja preenchidos + 3 .bat + LEIA-ME.
"""
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from auth import get_current_user
from . import token as token_mod


router = APIRouter(prefix="/agente", tags=["agente-download"])

# Paths do backend
AGENTE_DIR = Path(__file__).parent
BACKEND_DIR = AGENTE_DIR.parent
SERVICES_DIR = BACKEND_DIR / "services"
PACOTE_DIR = AGENTE_DIR / "pacote"


def _panel_url_from_request(request: Request) -> str:
    # X-Forwarded-Proto/Host (Railway/Nginx atras de proxy)
    xf_proto = request.headers.get("x-forwarded-proto")
    xf_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if xf_proto and xf_host:
        return f"{xf_proto}://{xf_host}"
    return str(request.base_url).rstrip("/")


_STUB_BROWSER_CONFIG = '''"""Stub do browser_config para o agente.

O agente NAO faz launch — usa CDP attach. Este stub existe so pra
excel_processor.py importar sem erro.
"""
IS_RAILWAY = False


def launch_kwargs(headless: bool = True, extra_args=None) -> dict:
    return {"headless": headless, "channel": "chrome", "args": []}
'''

_STUB_CONFIG_MANAGER = '''"""Stub do config_manager para o agente.

No modo agente, o Chrome ja esta logado — nao precisamos de credencial
armazenada. As funcoes retornam vazio; se qualquer motor tentar chamar
`get_credencial` isso indica bug (agente nao devia fazer login).
"""


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

Instale uma vez, use sempre. Deixe as 3 janelas abertas para o agente
puxar ordens do painel automaticamente.

INSTALACAO (uma vez)
--------------------
1) Rode "1 - INSTALAR.bat" (com duplo clique).
   - Detecta/instala Python 3.12 (via winget) e as dependencias
     (playwright, pandas, openpyxl, httpx, certifi).
   - Nao roda "playwright install chromium" — o agente usa o Chrome real.

USO (toda vez)
--------------
2) Rode "2 - ABRIR CHROME.bat".
   - Abre uma janela do Chrome com --remote-debugging-port=9222 e um
     perfil separado (nao mistura com seu Chrome pessoal).
   - Faca login no GW e nos portais das factories que voce usa. Uma unica
     vez. As sessoes ficam salvas no perfil.

3) Rode "3 - INICIAR AGENTE.bat".
   - Comeca a puxar ordens do painel a cada 5 segundos.
   - Quando um usuario disparar uma operacao no painel (com AGENTE_ATIVO
     ligado), a execucao acontece AQUI, no Chrome ja logado.

Deixe as duas janelas (Chrome + Agente) abertas enquanto usar. Fechar o
Chrome interrompe a sessao. Se precisar reiniciar, feche o Chrome, rode
o passo (2) de novo e refaca o login se pedir.

DIAGNOSTICO
-----------
- No painel, veja "Agente online" (verde) se o ping esta chegando.
- Se ficar "offline", verifique se "3 - INICIAR AGENTE" continua rodando.
- Log do agente aparece na propria janela dele.
"""


def _read_or_empty(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _read_bat(nome: str) -> str:
    """Le .bat da pasta pacote/ ou retorna placeholder se ausente."""
    p = PACOTE_DIR / nome
    return _read_or_empty(p) or f"echo Arquivo {nome} ausente no build do painel.\r\npause\r\n"


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
        # ── Raiz do pacote ──
        zf.writestr("LEIA-ME.txt", _leia_me(panel_url))
        zf.writestr("1 - INSTALAR.bat", _read_bat("1 - INSTALAR.bat"))
        zf.writestr("2 - ABRIR CHROME.bat", _read_bat("2 - ABRIR CHROME.bat"))
        zf.writestr("3 - INICIAR AGENTE.bat", _read_bat("3 - INICIAR AGENTE.bat"))

        # ── Pasta agente/ ──
        zf.writestr("agente/agente_config.json", json.dumps(config, indent=2, ensure_ascii=False))
        zf.writestr("agente/__init__.py", "")

        for nome in ("agente_bot.py", "motor_excel.py"):
            zf.writestr(f"agente/{nome}", _read_or_empty(AGENTE_DIR / nome))

        # Stubs para o excel_processor importar sem erro
        zf.writestr("agente/browser_config.py", _STUB_BROWSER_CONFIG)
        zf.writestr("agente/config_manager.py", _STUB_CONFIG_MANAGER)
        zf.writestr("agente/_tz.py", _read_or_empty(BACKEND_DIR / "_tz.py"))

        # services: original + attach
        zf.writestr("agente/services/__init__.py", "")
        zf.writestr("agente/services/excel_processor.py",
                    _read_or_empty(SERVICES_DIR / "excel_processor.py"))
        zf.writestr("agente/services/excel_processor_attach.py",
                    _read_or_empty(SERVICES_DIR / "excel_processor_attach.py"))

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="AutoFactory-Agente.zip"'},
    )
