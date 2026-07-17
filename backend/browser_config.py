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


# Args essenciais pra Chromium rodar em container Linux (Railway):
# - --no-sandbox: obrigatorio em container sem privilegios (senao Chromium crasha)
# - --disable-dev-shm-usage: /dev/shm eh minusculo em container (~64MB); forca usar /tmp
# - --disable-gpu: sem GPU no container
# - --disable-setuid-sandbox: reforca no-sandbox
# - --single-process: reduz consumo de memoria (pode ser instavel; testar)
# Sem essas flags, launch da "Connection closed while reading from the driver".
_RAILWAY_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
]


def launch_kwargs(headless: bool = True, extra_args: list[str] | None = None) -> dict:
    """
    Retorna kwargs para `p.chromium.launch(**kw)`.
    - Local: usa channel='chrome' (Chrome branded instalado no PC), respeita headless passado
    - Railway: ignora channel (só Chromium), força headless=True + args de container
    """
    if IS_RAILWAY:
        kw: dict = {"headless": True, "args": list(_RAILWAY_ARGS)}
    else:
        kw = {"headless": headless, "channel": "chrome", "args": []}
    if extra_args:
        kw["args"] = kw.get("args", []) + list(extra_args)
    return kw
