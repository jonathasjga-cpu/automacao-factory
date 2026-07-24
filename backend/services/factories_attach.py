"""Variante ATTACH das 3 factories (Firma / FluxAsset / GC).

Nao faz launch nem login — conecta ao Chrome que o usuario ja abriu
e logou em cada factory (uma vez). Reusa os cores das originais.

Execucao SEQUENCIAL entre factories: paralelizar 3 factories no mesmo
Chrome CDP compartilha cookies e pode confundir sessoes (ex: fluxasset_matriz
+ fluxasset_sp usam mesmo dominio). Sequencial e' seguro e mais rapido
que abrir 3 launches separados.
"""
import os
from typing import Callable, Optional

from playwright.async_api import async_playwright

from services.firma_automation import _core_executar_firma, fazer_login_firma
from services.fluxasset_automation import _core_executar_fluxasset, fazer_login_fluxasset
from services.gc_automation import (
    _core_gerar_remessa_gw,
    _core_executar_gc_portal,
    CONTA_POR_SISTEMA,
    fazer_login_gc,
)


CDP_URL_DEFAULT = os.getenv("CDP_URL", "http://localhost:9222")
BASE_GW_DEFAULT = os.getenv("GW_BASE_URL", "https://webtrans.saas2.gwsistemas.com.br")


def _sistema_para_url(sistema: str) -> str:
    """URL onde a factory ja deve estar logada no Chrome CDP."""
    if sistema.startswith("firma"):
        return "intrafac777.firmasa.com"
    if sistema.startswith("fluxasset"):
        return "portal.fluxasset.com.br"
    if sistema.startswith("gc"):
        return "gcrecursos.dyndns.org"
    return ""


async def _login_gw_se_precisar(page, base_gw: str, status: dict) -> None:
    """Loga no GW se a aba estiver em /login OU se responder 401 (sessao expirada)."""
    url_atual = (page.url or "").lower()
    precisa_login = "login" in url_atual
    # Se URL parece OK, verifica se a pagina tem 401 no title/body (sessao expirou)
    if not precisa_login:
        try:
            titulo = (await page.title() or "").lower()
            if "401" in titulo or "not authorized" in titulo or "acesso negado" in titulo:
                precisa_login = True
        except Exception:
            pass
    if not precisa_login:
        return
    log = lambda msg: status.setdefault("logs", []).append(msg)
    try:
        from config_manager import get_credencial
        creds = get_credencial("gw", user_id=status.get("usuario_id"))
        if not creds.get("usuario") or not creds.get("senha"):
            log("  ⚠️ [GW] Sem credenciais salvas — nao vou logar automaticamente")
            return
        log("  [LOGIN] GW — logando...")
        await page.goto(f"{base_gw}/login", wait_until="domcontentloaded", timeout=30000)
        await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
        await page.locator('input[name="login"]').fill(creds["usuario"])
        await page.locator('input[name="senha"]').fill(creds["senha"])
        await page.locator('button.button-login').click()
        try:
            await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        except Exception:
            pass
        log("  [LOGIN] GW OK")
    except Exception as e:
        log(f"  ⚠️ [GW] Falha ao logar: {e}")


async def _achar_ou_abrir_page(browser, url_marker: str, url_home: str):
    """Reusa aba ja aberta no dominio, ou cria nova."""
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if url_marker in (pg.url or "").lower():
                return ctx, pg
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = await ctx.new_page()
    await page.goto(url_home, wait_until="load", timeout=60000)
    return ctx, page


async def executar_factory_attach(
    sistema: str,
    faturas_selecao,
    status: dict,
    report: Optional[Callable] = None,
    cdp_url: str = CDP_URL_DEFAULT,
    base_gw: str = BASE_GW_DEFAULT,
) -> dict:
    """Executa UMA factory via CDP attach.
    Assume Chrome CDP com sessao aberta em cada portal.
    """
    if report is None:
        report = lambda *_: None
    log = lambda msg: status.setdefault("logs", []).append(msg)

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            raise Exception(
                f"Nao foi possivel conectar CDP em {cdp_url}. Rode "
                f"'2 - ABRIR CHROME.bat' primeiro. Detalhe: {str(e)[:150]}"
            )

        try:
            if sistema.startswith("firma"):
                ctx, page = await _achar_ou_abrir_page(
                    browser, "firmasa.com",
                    "https://intrafac777.firmasa.com/Factadebentures/login",
                )
                # SEMPRE loga forcado — garante credencial correta por filial.
                # Firma Matriz e Firma SP usam mesmo dominio (cookies compartilhados
                # no CDP), entao sessao aberta pode nao ser a do sistema atual.
                log(f"  [LOGIN] Firma {sistema} — forcando login com credencial da filial...")
                await fazer_login_firma(page, sistema)
                report(0, 1, f"executando Firma {sistema}...")
                await _core_executar_firma(page, faturas_selecao, sistema, status)

            elif sistema.startswith("fluxasset"):
                ctx, page = await _achar_ou_abrir_page(
                    browser, "fluxasset.com.br",
                    "https://portal.fluxasset.com.br/Factaconsult/login",
                )
                log(f"  [LOGIN] FluxAsset {sistema} — forcando login com credencial da filial (pode pedir Cloudflare)...")
                await fazer_login_fluxasset(page, sistema, status)
                report(0, 1, f"executando FluxAsset {sistema}...")
                await _core_executar_fluxasset(page, faturas_selecao, sistema, status)

            elif sistema.startswith("gc"):
                # GC precisa de 2 etapas: gerar .rem no GW + operar no portal GC
                # Etapa 1 — reusa aba GW (mesma do carregar/documentos)
                ctx_gw, page_gw = await _achar_ou_abrir_page(
                    browser, "webtrans", f"{base_gw}/home",
                )
                # Se GW nao esta logado, faz login usando credenciais salvas
                await _login_gw_se_precisar(page_gw, base_gw, status)
                report(0, 3, f"GC {sistema} — gerando .rem no GW...")
                numeros = [sel.numero for sel in faturas_selecao]
                numeros_norm = [n.zfill(6) for n in numeros]
                caminho_rem = await _core_gerar_remessa_gw(page_gw, ctx_gw, numeros_norm, sistema, status)
                if not caminho_rem:
                    status.setdefault("erros", []).append(f"GC {sistema}: falha ao gerar .rem")
                    return {"sistema": sistema}
                # Etapa 2 — portal GC
                ctx_gc, page_gc = await _achar_ou_abrir_page(
                    browser, "gcrecursos.dyndns.org",
                    "http://gcrecursos.dyndns.org:9000/FactaConsult",
                )
                # SEMPRE loga forcado no GC — Matriz e SP usam mesmo dominio
                log(f"  [LOGIN] GC {sistema} — forcando login com credencial da filial...")
                await fazer_login_gc(page_gc, sistema)
                report(1, 3, f"GC {sistema} — operando no portal...")
                total_qtd = len(numeros)
                await _core_executar_gc_portal(page_gc, faturas_selecao, sistema, status, caminho_rem, numeros, total_qtd)
                report(2, 3, f"GC {sistema} concluida")

            else:
                raise Exception(f"Sistema desconhecido: {sistema}")

        finally:
            # NAO fecha browser — sessao do user continua aberta
            pass

    return {"sistema": sistema}


async def executar_factories_attach(
    faturas_por_factory_selecao: dict,
    status: dict,
    report: Optional[Callable] = None,
) -> dict:
    """Executa TODAS as factories na ordem — sequencial, no CDP compartilhado.
    faturas_por_factory_selecao: {sistema: [FaturaSelecao(numero, factory), ...]}
    """
    if report is None:
        report = lambda *_: None
    total = len(faturas_por_factory_selecao)
    idx = 0
    for sistema, sel_list in faturas_por_factory_selecao.items():
        idx += 1
        report(idx - 1, total, f"iniciando {sistema}...")
        try:
            await executar_factory_attach(sistema, sel_list, status, report=lambda f,t,d: report(idx-1, total, f"{sistema}: {d}"))
        except Exception as e:
            status.setdefault("logs", []).append(f"❌ {sistema} falhou: {e}")
            status.setdefault("erros", []).append(f"{sistema}: {e}")
        report(idx, total, f"{sistema} concluida")
    return status
