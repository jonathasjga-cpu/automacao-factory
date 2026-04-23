"""
Centraliza argumentos de launch do Playwright para funcionar em dois ambientes:
- Local (Windows do usuário): Chrome branded visível/headless conforme necessário
- Railway (container Linux): apenas Chromium da imagem oficial, sempre headless
"""
import os

# Detecção do Railway (qualquer uma dessas vars indica execução lá)
IS_RAILWAY = any(os.environ.get(k) for k in (
    "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"
))


def launch_kwargs(headless: bool = True, extra_args: list[str] | None = None) -> dict:
    """
    Retorna kwargs para `p.chromium.launch(**kw)`.
    - Local: usa channel='chrome' (Chrome branded instalado no PC), respeita headless passado
    - Railway: ignora channel (só Chromium), força headless=True
    """
    if IS_RAILWAY:
        kw: dict = {"headless": True}
    else:
        kw = {"headless": headless, "channel": "chrome"}
    if extra_args:
        kw["args"] = list(extra_args)
    return kw
