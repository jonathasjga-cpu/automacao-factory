"""Variante ATTACH do excel_processor.

Em vez de p.chromium.launch() + login, conecta via CDP ao Chrome que o
usuario ja abriu (uma vez) na maquina local. Reusa TODA a logica de
geracao/download/parse das planilhas do modulo original.

Como funciona:
- O usuario rodou '2 - ABRIR CHROME.bat' que abriu um Chrome com
  --remote-debugging-port=9222 e --user-data-dir=<perfil isolado>.
- Ele logou no GW manualmente (uma vez). Sessao fica no perfil.
- Aqui: connect_over_cdp -> reusa a aba (ou abre nova) -> gera relatorios.

Nao faz login. Se detectar tela de login, informa erro clarinho.
"""
import asyncio
import os
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import async_playwright

# Reusa TODAS as funcoes puras do modulo original.
from services.excel_processor import (
    _gerar_relatorio_personalizado,
    _aguardar_e_baixar,
    processar_dataframes,
)


CDP_URL_DEFAULT = os.getenv("CDP_URL", "http://localhost:9222")
BASE_GW_DEFAULT = os.getenv("GW_BASE_URL", "https://webtrans.saas2.gwsistemas.com.br")


def _hoje_br() -> str:
    """DD/MM/AAAA — timezone local. No agente rodamos na maquina do usuario
    entao 'hoje' local eh igual a hoje-BR na maior parte do tempo."""
    from datetime import datetime
    return datetime.now().strftime("%d/%m/%Y")


async def _abrir_aba_automacao_gw(browser, base_gw: str):
    """Prefere REUSAR aba GW existente (herda sessionStorage do login manual).
    So abre nova se nao houver aba webtrans. Retorna (ctx, page, nova) — se
    nova=True o chamador fecha no finally; se nova=False deixa (é do usuario)."""
    contexts = browser.contexts
    if not contexts:
        raise Exception(
            "Nenhum contexto CDP disponivel. Rode '2 - ABRIR CHROME.bat' primeiro "
            "e deixe a janela aberta."
        )
    for ctx in contexts:
        for pg in ctx.pages:
            if "webtrans" in (pg.url or "").lower():
                return ctx, pg, False
    ctx = contexts[0]
    page = await ctx.new_page()
    await page.goto(f"{base_gw}/home", wait_until="load", timeout=60000)
    return ctx, page, True


async def _fechar_aba_automacao(page):
    try:
        await page.close()
    except Exception:
        pass


async def _fazer_login_gw(page, base_gw: str) -> None:
    """Loga no GW usando credenciais do config_manager.
    O stub config_manager.py do agente le _agente_credenciais_atuais.json,
    escrito pelo motor_excel.py a partir do payload da ordem."""
    from config_manager import get_credencial
    creds = get_credencial("gw") or {}
    usuario = creds.get("usuario") or ""
    senha = creds.get("senha") or ""
    if not usuario or not senha:
        raise Exception(
            "Credenciais GW nao configuradas no painel. Acesse Configuracoes > GW "
            "e salve usuario/senha antes de operar."
        )
    await page.goto(f"{base_gw}/login", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_selector('#login', timeout=15000)
    await page.fill('#login', usuario)
    await page.fill('#senha', senha)
    await page.click('.button-login')
    await page.wait_for_load_state("load", timeout=60000)
    await page.wait_for_timeout(2500)
    # Volta pra home pra estabilizar a sessao
    try:
        await page.goto(f"{base_gw}/home", wait_until="load", timeout=30000)
        await page.wait_for_timeout(1500)
    except Exception:
        pass


async def _garantir_logado(page, base_gw: str, report: Optional[Callable] = None):
    """Verifica que a sessao esta ativa. Se cair pra tela de login OU 403,
    tenta logar automaticamente usando credenciais salvas no painel."""
    try:
        # Deixa 2s pra qualquer redirect pos-navegacao terminar
        await page.wait_for_load_state("load", timeout=15000)
    except Exception:
        pass
    url = (page.url or "").lower()
    titulo = ""
    try:
        titulo = (await page.title() or "").lower()
    except Exception:
        pass
    precisa_login = ("login" in url) or ("login" in titulo) or ("403" in titulo) or ("403" in url)
    if precisa_login:
        if report:
            report(1, 3, "sessao GW expirada — logando automaticamente...")
        await _fazer_login_gw(page, base_gw)
        # Rechecagem apos login
        try:
            titulo2 = (await page.title() or "").lower()
        except Exception:
            titulo2 = ""
        url2 = (page.url or "").lower()
        if ("login" in url2) or ("403" in titulo2) or ("403" in url2):
            raise Exception(
                "GW retornou 403 mesmo apos re-login. Possiveis causas: credenciais "
                "sem permissao no modulo webtrans, rate-limit do GW ou IP do servidor "
                "bloqueado. Tente em alguns minutos ou verifique as credenciais no painel."
            )
        if report:
            report(1, 3, "login GW OK — prosseguindo...")
    else:
        if report:
            report(1, 3, f"sessao GW OK: {page.url[:80]}")


async def carregar_faturas_attach(
    data_inicial_br: str | None = None,
    data_final_br: str | None = None,
    report: Optional[Callable] = None,
    cdp_url: str = CDP_URL_DEFAULT,
    base_gw: str = BASE_GW_DEFAULT,
) -> list[dict]:
    """Baixa relatorios do GW usando o Chrome CDP ja aberto e logado.

    data_inicial_br / data_final_br em DD/MM/AAAA (ou None = hoje).
    """
    if report is None:
        report = lambda *_: None

    data_ini = data_inicial_br or _hoje_br()
    data_fim = data_final_br or data_ini
    meus_rel_url = f"{base_gw}/RelatorioControlador?acao=abrirTelaMeusRelatorios"

    async with async_playwright() as p:
        report(0, 3, f"conectando CDP em {cdp_url}...")
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            raise Exception(
                f"Nao foi possivel conectar ao Chrome em {cdp_url}. "
                "Rode '2 - ABRIR CHROME.bat' primeiro. Detalhe: {}".format(str(e)[:200])
            )

        ctx, page, nova = await _abrir_aba_automacao_gw(browser, base_gw)
        try:
            await _garantir_logado(page, base_gw, report)

            # Gera Automacao
            report(1, 3, f"gerando 'Automacao Operacoes' ({data_ini} -> {data_fim})...")
            await _gerar_relatorio_personalizado(
                page, "Automação Operações - Jonathas", data_ini, base_gw,
                context=ctx, data_final=data_fim,
            )
            report(1, 3, f"baixando 'Automacao Operacoes'...")
            arquivo1 = await _aguardar_e_baixar(
                page, ctx, "Automação Operações - Jonathas", meus_rel_url
            )

            # Gera Complemento (pagina ja esta na aba Personalizados)
            report(2, 3, f"gerando 'Complemento Operacoes' ({data_ini} -> {data_fim})...")
            try:
                await _gerar_relatorio_personalizado(
                    page, "Complemento Operações - Jonathas", data_ini, base_gw,
                    preencher_data=True, context=ctx, navegar=False, data_final=data_fim,
                )
                report(2, 3, "baixando 'Complemento Operacoes'...")
                arquivo2 = await _aguardar_e_baixar(
                    page, ctx, "Complemento Operações - Jonathas", meus_rel_url
                )
            except Exception as e:
                raise Exception(f"[Complemento] Falha: {e}")

            # Nao fechamos o browser — a sessao do usuario continua aberta.
            report(3, 3, "processando planilhas...")
            faturas = processar_dataframes(arquivo1, arquivo2)
            return faturas
        finally:
            if nova:
                await _fechar_aba_automacao(page)
