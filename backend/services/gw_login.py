# -*- coding: utf-8 -*-
"""Login do GW em UM lugar so.

Isto existia copiado em SETE pontos (excel_processor x2, excel_processor_attach,
documentos, documentos_attach, factories_attach, gc_automation), cada um com os
seletores escritos na mao. Quando o GW trocou o formulario de login, os sete
quebraram juntos e o usuario recebeu "Timeout waiting for #login" — mensagem que
aponta pro seletor e nao pra causa.

MEDIDO em 21/10/2026 na tela real: os campos agora sao name="email" e
name="password". Antes eram #login e #senha (e, em algumas telas,
name="login"/name="senha"). O botao continua .button-login.

Por isso a lista de seletores e' uma CASCATA, do atual pro antigo: se alguma
instancia ainda servir o formulario velho, continua funcionando. E quando mudar
de novo, muda AQUI — nao em sete arquivos.
"""
import asyncio
from typing import Callable, Optional

# Ordem importa: o primeiro VISIVEL na tela vence.
SELETORES_USUARIO = (
    'input[name="email"]',      # atual (21/10/2026)
    '#login',                   # anterior
    'input[name="login"]',      # anterior, em telas sem id
    'input[type="email"]',      # ultimo recurso, por tipo
)
SELETORES_SENHA = (
    'input[name="password"]',
    '#senha',
    'input[name="senha"]',
    'input[type="password"]',
)
SELETORES_BOTAO = (
    '.button-login',
    'button.button-login',
    'button[type="submit"]',
    'input[type="submit"]',
)


async def _primeiro_visivel(page, seletores, timeout_total: int = 20000):
    """Primeiro seletor VISIVEL na tela, ou None.

    Varre todos a cada volta em vez de esperar cada um ate o fim: esperar 15s
    por seletor numa lista de quatro daria um minuto antes de desistir.
    """
    fim = timeout_total / 1000.0
    gasto = 0.0
    while gasto < fim:
        for sel in seletores:
            try:
                if await page.locator(sel).first.is_visible(timeout=250):
                    return sel
            except Exception:
                pass
        await asyncio.sleep(0.3)
        gasto += 0.3
    return None


async def preencher_login_gw(
    page,
    usuario: str,
    senha: str,
    log: Optional[Callable] = None,
    timeout_campos: int = 20000,
) -> str:
    """Preenche e envia o login do GW na pagina JA aberta em /login.

    Devolve o seletor de usuario que funcionou. Levanta excecao com mensagem
    util quando nao ha formulario — o que normalmente significa sessao ja ativa
    (o GW redireciona pra /home) ou pagina de erro.
    """
    _log = log or (lambda _m: None)

    sel_user = await _primeiro_visivel(page, SELETORES_USUARIO, timeout_campos)
    if not sel_user:
        url = ""
        try:
            url = page.url or ""
        except Exception:
            pass
        if "login" not in url.lower():
            raise Exception(
                "A tela de login do GW nao apareceu porque a pagina saiu de /login "
                "(esta em " + url[:100] + "). Normalmente isso quer dizer que a "
                "sessao ja estava ativa — nao ha o que logar."
            )
        raise Exception(
            "O formulario de login do GW nao foi encontrado. Campos conhecidos: "
            + ", ".join(SELETORES_USUARIO)
            + ". Se o GW mudou a tela de novo, ajuste services/gw_login.py — o "
            "login fica so la."
        )

    sel_senha = await _primeiro_visivel(page, SELETORES_SENHA, 5000)
    if not sel_senha:
        raise Exception(
            "Achei o campo de usuario (" + sel_user + ") mas nao o de senha na "
            "tela de login do GW. A tela pode ter mudado — ver services/gw_login.py."
        )

    await page.locator(sel_user).first.fill(usuario)
    await page.locator(sel_senha).first.fill(senha)

    sel_botao = await _primeiro_visivel(page, SELETORES_BOTAO, 5000)
    if sel_botao:
        await page.locator(sel_botao).first.click()
    else:
        _log("  [GW] Botao de login nao encontrado — enviando com Enter")
        await page.locator(sel_senha).first.press("Enter")

    if sel_user != SELETORES_USUARIO[0]:
        _log("  [GW] Login pelo layout antigo (" + sel_user + ")")
    return sel_user
