"""Gestor central de abas do Chrome CDP — usado pelos modulos *_attach.

Modelo mental (igual ao do usuario): o '2 - ABRIR CHROME.bat' abre 4 abas
padrao (GW, Firma, FluxAsset, GC) e a automacao REUSA essas abas. Nunca
acumula abas novas, nunca fecha aba que o usuario abriu.

Regras:
  1. Reusa a PRIMEIRA aba de cada dominio (a aba padrao do .bat).
  2. Duplicatas do mesmo dominio (deixadas por execucoes anteriores que
     crasharam no meio) sao fechadas automaticamente — auto-limpeza.
  3. So cria aba nova se nao existir NENHUMA aba do dominio. Nesse caso o
     chamador decide se fecha ao final (flag `criada` no retorno).

IMPORTANTE: sessionStorage e' POR ABA. O login do GW depende de
sessionStorage — reusar a aba padrao preserva o login manual do usuario.
Abrir aba nova cai na tela de login mesmo com cookies validos.
"""
from typing import Optional


# Marca gravada em window.name nas abas que a AUTOMACAO abre. window.name
# sobrevive a navegacoes na mesma aba, entao leftovers de crash continuam
# identificaveis. Abas do usuario nunca tem essa marca — e nunca sao fechadas.
TAG_AUTOMACAO = "__autofactory_aba__"


async def _marcar_como_automacao(page) -> None:
    """Grava a marca em window.name (silencioso se a pagina nao permitir)."""
    try:
        await page.evaluate(f"() => {{ window.name = '{TAG_AUTOMACAO}'; }}")
    except Exception:
        pass


async def _eh_aba_da_automacao(page) -> bool:
    """True so se a aba foi criada pela automacao. Fail-safe: erro => False
    (na duvida NAO fecha, pra nunca derrubar aba do usuario)."""
    try:
        return bool(await page.evaluate(f"() => window.name === '{TAG_AUTOMACAO}'"))
    except Exception:
        return False


async def achar_aba(browser, marker: str, url_home: str, log=None):
    """Retorna (ctx, page, criada) pra o dominio identificado por `marker`.

    - Reusa a primeira aba cujo URL contem `marker` (case-insensitive),
      PREFERINDO uma aba do usuario (sem a marca) — e' a que tem a sessao
      logada manualmente.
    - Fecha somente duplicatas MARCADAS como da automacao (leftovers de crash).
      Abas que o usuario abriu no mesmo dominio ficam intactas.
    - Se nao ha nenhuma, abre aba nova em contexts[0], marca e navega.
    """
    _log = log or (lambda m: None)
    candidatas = []          # [(ctx, pg)] no dominio
    for ctx in browser.contexts:
        for pg in list(ctx.pages):
            if marker in (pg.url or "").lower():
                candidatas.append((ctx, pg))

    # Limpeza de popups orfaos: em kill duro (queda de energia, taskkill, fechar
    # o console do agente) os finally nao rodam e os popups de PDF sobrevivem no
    # Chrome persistente. Eles ficam em about:blank/S3, fora de qualquer marker,
    # entao precisam de varredura propria — so fecha os MARCADOS.
    for ctx in browser.contexts:
        for pg in list(ctx.pages):
            u = (pg.url or "").lower()
            if marker in u:
                continue  # tratado abaixo
            if not (u in ("", "about:blank") or "amazonaws.com" in u
                    or "gw-saas-relatorios" in u or u.startswith("chrome-error")):
                continue
            if await _eh_aba_da_automacao(pg):
                try:
                    await pg.close()
                    _log("  [ABAS] Fechado popup orfao da automacao")
                except Exception:
                    pass

    if candidatas:
        # Prefere aba do usuario (sem marca) — tem a sessao logada manualmente
        escolhida = None
        marcadas = []
        for ctx, pg in candidatas:
            if await _eh_aba_da_automacao(pg):
                marcadas.append((ctx, pg))
            elif escolhida is None:
                escolhida = (ctx, pg)
        if escolhida is None:
            # Todas sao da automacao — usa a primeira e as demais sao leftovers
            escolhida = marcadas[0]
            marcadas = marcadas[1:]
        # Auto-limpeza: fecha leftovers da AUTOMACAO (nunca abas do usuario)
        for _c, pg in marcadas:
            try:
                await pg.close()
                _log(f"  [ABAS] Fechada aba leftover da automacao em '{marker}'")
            except Exception:
                pass
        return escolhida[0], escolhida[1], False

    if not browser.contexts:
        raise Exception(
            "Nenhum contexto CDP disponivel. Rode '2 - ABRIR CHROME.bat' primeiro "
            "e deixe a janela aberta."
        )
    ctx = browser.contexts[0]
    page = await ctx.new_page()
    await _marcar_como_automacao(page)
    try:
        await page.goto(url_home, wait_until="load", timeout=60000)
    except Exception:
        # Sem isso a aba recem-criada vazava: a excecao propagava antes de
        # retornar a tupla, e nenhum chamador tinha referencia pra fechar.
        await fechar_aba_criada(page)
        raise
    await _marcar_como_automacao(page)  # re-marca (goto pode limpar window.name)
    _log(f"  [ABAS] Aba de '{marker}' nao existia — criei nova")
    return ctx, page, True


async def fechar_aba_criada(page) -> None:
    """Fecha aba que a automacao CRIOU (criada=True). Silencioso se falhar."""
    try:
        await page.close()
    except Exception:
        pass


async def limpar_sessao_dominio(page, dominio: str) -> None:
    """Desloga a sessao persistida de um dominio: limpa cookies do dominio
    + localStorage/sessionStorage da aba. Usado quando /login redireciona
    pro dashboard (sessao antiga de outra filial no cdp_profile) e o form
    de login nunca aparece.

    `dominio` e' a base (ex: "firmasa.com") — cookies podem estar registrados
    em ".firmasa.com", "firmasa.com" ou "intrafac777.firmasa.com"; o filtro
    por regex cobre todas as variantes. `page` deve estar em alguma URL do
    dominio (pra limpar o storage da origem certa).
    """
    import re as _re
    # 1. Storage da aba (localStorage guarda token de SPAs; sessionStorage por via das duvidas)
    try:
        await page.evaluate(
            "() => { try { localStorage.clear(); } catch(e) {} "
            "try { sessionStorage.clear(); } catch(e) {} }"
        )
    except Exception:
        pass
    # 2. Cookies do dominio — regex pega subdominios e o prefixo "." tambem
    try:
        ctx = page.context
        try:
            await ctx.clear_cookies(domain=_re.compile(_re.escape(dominio)))
        except TypeError:
            # Playwright antigo sem filtro por domain: fallback JS (so pega
            # cookies nao-HttpOnly, mas melhor que nada).
            await page.evaluate(
                """() => {
                    document.cookie.split(';').forEach(c => {
                        const nome = c.split('=')[0].trim();
                        document.cookie = nome + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
                    });
                }"""
            )
    except Exception:
        pass
