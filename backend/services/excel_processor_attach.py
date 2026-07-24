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


async def _encontrar_ou_abrir_pagina_gw(browser, base_gw: str):
    """Tenta reusar aba ja aberta no GW. Se nao houver, abre em novo contexto."""
    contexts = browser.contexts
    if not contexts:
        raise Exception(
            "Nenhum contexto CDP disponivel. Rode '2 - ABRIR CHROME.bat' primeiro "
            "e deixe a janela aberta."
        )
    # Procura em qualquer contexto uma aba ja no GW
    for ctx in contexts:
        for pg in ctx.pages:
            u = (pg.url or "").lower()
            if "webtrans" in u and "login" not in u:
                return ctx, pg
    # Nao achou aba logada — abre nova aba no primeiro contexto
    ctx = contexts[0]
    page = await ctx.new_page()
    await page.goto(f"{base_gw}/home", wait_until="load", timeout=60000)
    return ctx, page


async def _garantir_logado(page, base_gw: str, report: Optional[Callable] = None):
    """Verifica que a sessao esta ativa. Se cair pra tela de login, orienta."""
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
    if "login" in url or "login" in titulo:
        raise Exception(
            "Chrome esta na tela de login do GW. Faca login manualmente na aba "
            "que abriu com '2 - ABRIR CHROME.bat' e reenvie a operacao."
        )
    if "403" in titulo or "403" in url:
        raise Exception(
            "GW retornou 403 nesta sessao. Faca logout+login manualmente no "
            "Chrome aberto e reenvie a operacao."
        )
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

        ctx, page = await _encontrar_ou_abrir_pagina_gw(browser, base_gw)
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
