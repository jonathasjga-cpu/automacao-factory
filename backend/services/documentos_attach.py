"""Variante ATTACH do documentos.py (baixar boletos + CTes via CDP).

Nao faz launch nem login — conecta ao Chrome que o usuario ja abriu
(uma vez) via `2 - ABRIR CHROME.bat` e reusa TODA a logica core dos
downloads do modulo original.
"""
import os
import traceback
from typing import Callable, Optional

from playwright.async_api import async_playwright

from services.documentos import (
    _core_baixar_faturas_pdf,
    _core_baixar_ctes_pdf,
)


CDP_URL_DEFAULT = os.getenv("CDP_URL", "http://localhost:9222")
BASE_GW_DEFAULT = os.getenv("GW_BASE_URL", "https://webtrans.saas2.gwsistemas.com.br")


async def _encontrar_page_gw(browser, base_gw: str):
    """Reusa aba GW existente (preferindo aba logada; senao, reusa a de login).
    So abre nova se nao existir NENHUMA aba webtrans no Chrome CDP."""
    contexts = browser.contexts
    if not contexts:
        raise Exception(
            "Nenhum contexto CDP disponivel. Rode '2 - ABRIR CHROME.bat' primeiro."
        )
    aba_login = None
    for ctx in contexts:
        for pg in ctx.pages:
            u = (pg.url or "").lower()
            if "webtrans" not in u:
                continue
            if "login" not in u:
                return ctx, pg
            if aba_login is None:
                aba_login = (ctx, pg)
    if aba_login is not None:
        return aba_login
    ctx = contexts[0]
    page = await ctx.new_page()
    await page.goto(f"{base_gw}/home", wait_until="load", timeout=60000)
    return ctx, page


async def _garantir_logado(page, report=None, base_gw: str = BASE_GW_DEFAULT, status: dict | None = None):
    try:
        await page.wait_for_load_state("load", timeout=15000)
    except Exception:
        pass
    url = (page.url or "").lower()
    precisa_login = "login" in url
    if not precisa_login:
        try:
            titulo = (await page.title() or "").lower()
            if "401" in titulo or "not authorized" in titulo or "acesso negado" in titulo:
                precisa_login = True
        except Exception:
            pass
    if precisa_login:
        # Tenta login automatico usando credenciais salvas
        try:
            from config_manager import get_credencial
            creds = get_credencial("gw", user_id=(status or {}).get("usuario_id"))
            if creds.get("usuario") and creds.get("senha"):
                if status:
                    status.setdefault("logs", []).append("  [LOGIN] GW — logando automaticamente...")
                await page.goto(f"{base_gw}/login", wait_until="domcontentloaded", timeout=30000)
                await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
                await page.locator('input[name="login"]').fill(creds["usuario"])
                await page.locator('input[name="senha"]').fill(creds["senha"])
                await page.locator('button.button-login').click()
                try:
                    await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
                except Exception:
                    pass
                url = (page.url or "").lower()
        except Exception as e:
            if status:
                status.setdefault("logs", []).append(f"  ⚠️ Falha ao logar no GW: {e}")

    if "login" in url:
        raise Exception(
            "Chrome esta na tela de login do GW e nao consegui logar "
            "automaticamente. Verifique as credenciais em Configuracoes."
        )
    if report:
        try: report(1, 4, f"sessao GW OK: {page.url[:80]}")
        except Exception: pass


async def baixar_documentos_attach(
    faturas_por_factory: dict[str, list[dict]],
    status: dict,
    report: Optional[Callable] = None,
    cdp_url: str = CDP_URL_DEFAULT,
    base_gw: str = BASE_GW_DEFAULT,
) -> dict:
    """Executa boletos PDF + CTes PDF/ZIP via CDP attach.

    Retorna o proprio `status` (contem arquivos, resumo_documentos, logs).
    """
    log = lambda msg: status.setdefault("logs", []).append(msg)
    if report is None:
        report = lambda *_: None

    report(0, 4, f"conectando CDP em {cdp_url}...")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            raise Exception(
                f"Nao foi possivel conectar ao Chrome em {cdp_url}. "
                f"Rode '2 - ABRIR CHROME.bat' primeiro. Detalhe: {str(e)[:200]}"
            )

        ctx, page = await _encontrar_page_gw(browser, base_gw)
        await _garantir_logado(page, report, base_gw=base_gw, status=status)

        # ETAPA 1 — Boletos PDF (Modelo 10)
        report(1, 4, "baixando PDF das faturas (Modelo 10)...")
        log("📄 ETAPA 1: Baixando PDF das faturas (Modelo 10)...")
        try:
            await _core_baixar_faturas_pdf(page, ctx, faturas_por_factory, status)
        except Exception as e:
            log(f"❌ ETAPA 1 falhou com excecao: {e}")
            log(traceback.format_exc()[-600:])

        # ETAPA 2 — CTes PDF + ZIP
        report(2, 4, "baixando CTes e criando ZIPs...")
        log("📋 ETAPA 2: Baixando CTes e criando ZIPs...")
        try:
            await _core_baixar_ctes_pdf(page, ctx, faturas_por_factory, status)
        except Exception as e:
            log(f"❌ ETAPA 2 falhou com excecao: {e}")
            log(traceback.format_exc()[-600:])

        # Nao fechamos o browser — sessao do usuario continua ativa.
        rd = status.get("resumo_documentos", {})
        total_arquivos = len(status.get("arquivos", {}))
        report(3, 4, f"gerados {total_arquivos} arquivo(s)")
        log(f"✅ {total_arquivos} arquivo(s) prontos")
        return status
