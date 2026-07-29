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
    # A aba reusada carrega um snapshot ANTIGO — se a sessao expirou no servidor
    # enquanto a aba ficava parada em /home, nada na pagina indica isso. Uma
    # navegacao leve exercita a sessao: valida -> /home carrega; expirada ->
    # redireciona pro login e o bloco abaixo detecta. Navegar na MESMA aba
    # preserva o sessionStorage de que o login do GW depende.
    try:
        await page.goto(f"{base_gw}/home", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
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


from services.cdp_tabs import achar_aba as _abrir_aba_automacao, fechar_aba_criada as _fechar_aba_automacao


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

        # Abas que a automacao ABRIU (nao as que reusou) — fechadas no finally
        # pra nao acumular. As abas do usuario permanecem intocadas.
        abas_criadas: list = []
        try:
            if sistema.startswith("firma"):
                ctx, page, nova = await _abrir_aba_automacao(
                    browser,
                    "firmasa.com",
                    "https://intrafac777.firmasa.com/Factadebentures/login",
                )
                if nova:
                    abas_criadas.append(page)
                # SEMPRE loga forcado — garante credencial correta por filial.
                # Firma Matriz e Firma SP usam mesmo dominio (cookies compartilhados
                # no CDP), entao sessao aberta pode nao ser a do sistema atual.
                log(f"  [LOGIN] Firma {sistema} — forcando login com credencial da filial...")
                await fazer_login_firma(page, sistema)
                report(0, 1, f"executando Firma {sistema}...")
                await _core_executar_firma(page, faturas_selecao, sistema, status)

            elif sistema.startswith("fluxasset"):
                ctx, page, nova = await _abrir_aba_automacao(
                    browser,
                    "fluxasset.com.br",
                    "https://portal.fluxasset.com.br/Factaconsult/login",
                )
                if nova:
                    abas_criadas.append(page)
                log(f"  [LOGIN] FluxAsset {sistema} — forcando login com credencial da filial (pode pedir Cloudflare)...")
                await fazer_login_fluxasset(page, sistema, status)
                report(0, 1, f"executando FluxAsset {sistema}...")
                await _core_executar_fluxasset(page, faturas_selecao, sistema, status)

            elif sistema.startswith("gc"):
                # GC precisa de 2 etapas: gerar .rem no GW + operar no portal GC
                # Etapa 1 — reusa aba GW ja logada do usuario (sessionStorage
                # do GW mora na aba; abrir aba nova cai na tela de login).
                ctx_gw, page_gw, nova_gw = await _abrir_aba_automacao(
                    browser, "webtrans", f"{base_gw}/home",
                )
                if nova_gw:
                    abas_criadas.append(page_gw)
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
                ctx_gc, page_gc, nova_gc = await _abrir_aba_automacao(
                    browser,
                    "gcrecursos.dyndns.org",
                    "http://gcrecursos.dyndns.org:9000/FactaConsult",
                )
                if nova_gc:
                    abas_criadas.append(page_gc)
                # Matriz e SP compartilham dominio: se a sessao da unidade
                # anterior ainda esta viva, /login redireciona pro sistema e o
                # fazer_login_gc estoura timeout esperando o campo #Email.
                # Limpa a sessao ANTES (mesmo tratamento da Firma). Nao mexe em
                # gc_automation.py — a logica de operacao da GC fica intacta.
                try:
                    await page_gc.goto(
                        "http://gcrecursos.dyndns.org:9000/FactaConsult/login",
                        wait_until="domcontentloaded", timeout=90000,
                    )
                    await page_gc.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                if "login" not in (page_gc.url or "").lower():
                    log(f"  [LOGIN] GC ja tinha sessao ativa — limpando pra logar como {sistema}...")
                    from services.cdp_tabs import limpar_sessao_dominio
                    await limpar_sessao_dominio(page_gc, "gcrecursos.dyndns.org")
                # Guard de credencial AQUI (nao dentro de gc_automation.py, pra
                # nao tocar na logica da operacao da GC): erro claro em vez de
                # timeout obscuro esperando o campo #Email.
                try:
                    from config_manager import get_credencial as _gc_cred
                    _c = _gc_cred(sistema) or {}
                    if not (_c.get("usuario") or "").strip() or not (_c.get("senha") or ""):
                        raise Exception(
                            f"Credenciais da GC ({sistema}) nao cadastradas. Acesse "
                            f"Configuracoes e salve usuario/senha antes de operar."
                        )
                except ImportError:
                    pass
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
            # Fecha SO as abas que a automacao criou.
            for p in abas_criadas:
                await _fechar_aba_automacao(p)

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
    status.setdefault("logs", [])
    status.setdefault("erros", [])
    status.setdefault("faturas_salvas", set())
    status["concluidas"] = status.get("concluidas", 0)
    total = len(faturas_por_factory_selecao)
    idx = 0
    for sistema, sel_list in faturas_por_factory_selecao.items():
        idx += 1
        report(idx - 1, total, f"iniciando {sistema}...")
        # Sub-status POR SISTEMA: compartilha logs/erros/arquivos/faturas_cache
        # (mesmas referencias), mas faturas_salvas e concluidas sao proprios.
        # Antes o set global acumulava faturas de TODAS as unidades e a
        # validacao de valor da 2a unidade somava faturas da 1a -> divergencia
        # falsa ("valor da operacao nao confere") e a operacao era abortada.
        sub = dict(status)
        sub["faturas_salvas"] = set()
        sub["concluidas"] = 0
        try:
            await executar_factory_attach(sistema, sel_list, sub, report=lambda f,t,d: report(idx-1, total, f"{sistema}: {d}"))
        except Exception as e:
            status["logs"].append(f"❌ {sistema} falhou: {e}")
            status["erros"].append(f"{sistema}: {e}")
        finally:
            # Mescla de volta os contadores do sistema no total da operacao
            status["concluidas"] += int(sub.get("concluidas", 0) or 0)
            try:
                status["faturas_salvas"] |= set(sub.get("faturas_salvas") or set())
            except Exception:
                pass
        report(idx, total, f"{sistema} concluida")
    return status
