import asyncio
import re
import httpx
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page
from config_manager import get_credencial


def _data_operacao_str() -> str:
    """Retorna a data que a factory registra para a operação (próxima segunda em fins de semana)."""
    hoje = datetime.now()
    if hoje.weekday() == 5:   # sábado
        return (hoje + timedelta(days=2)).strftime("%d/%m/%Y")
    elif hoje.weekday() == 6:  # domingo
        return (hoje + timedelta(days=1)).strftime("%d/%m/%Y")
    return hoje.strftime("%d/%m/%Y")


FLUXASSET_URL = "https://portal.fluxasset.com.br/Factaconsult"


async def fazer_login_fluxasset(page: Page, sistema: str):
    """Faz login na FluxAsset (usa Chrome real para passar o Cloudflare Turnstile)"""
    creds = get_credencial(sistema)
    await page.goto(f"{FLUXASSET_URL}/login", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_selector('input[type="password"]', timeout=45000)
    await page.fill('input[type="text"], input[type="email"], input[placeholder*="suário"]', creds["usuario"])
    await page.fill('input[type="password"]', creds["senha"])
    await page.click('button:has-text("Entrar"), button[type="submit"]')
    try:
        await page.wait_for_selector('nav, .navbar, a[href*="operacao"]', timeout=30000)
    except Exception:
        await page.wait_for_timeout(3000)


async def navegar_para_digitacao(page: Page):
    """Navega para Digitação via JS (link dentro do dropdown oculto)"""
    await page.evaluate(
        "() => { const a = document.querySelector('a[href*=\"/operacao/digitacao\"]'); if(a) a.click(); }"
    )
    await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(1500)


async def cadastrar_sacado_se_necessario(page: Page, fatura: dict, status: dict):
    """Verifica se abriu tela de cadastro e preenche se necessário"""
    log = lambda msg: status["logs"].append(msg)

    cadastro_aberto = await page.query_selector('text=Cadastro de sacado')
    if not cadastro_aberto:
        return

    log(f"  📋 Cliente {fatura['cliente_cnpj']} não cadastrado. Buscando na Receita Federal...")

    cnpj_limpo = re.sub(r'\D', '', fatura["cliente_cnpj"])
    dados = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://publica.cnpj.ws/cnpj/{cnpj_limpo}")
            if resp.status_code == 200:
                d = resp.json()
                razao = d.get("razao_social", "")
                end = d.get("estabelecimento", {})
                primeiro = razao.split()[0].lower() if razao else "empresa"
                dados = {
                    "nome": razao,
                    "cep": re.sub(r'\D', '', end.get("cep", "")),
                    "endereco": end.get("logradouro", ""),
                    "cidade": end.get("cidade", {}).get("nome", "") if isinstance(end.get("cidade"), dict) else end.get("cidade", ""),
                    "uf": end.get("estado", {}).get("sigla", "") if isinstance(end.get("estado"), dict) else end.get("estado", ""),
                    "email": f"{primeiro}@gmail.com",
                }
                log(f"  ✅ Dados encontrados: {razao}")
    except Exception:
        log(f"  ⚠️ Não foi possível buscar CNPJ. Usando nome da fatura.")

    nome = dados.get("nome") or fatura["cliente_nome"]
    primeiro = nome.split()[0].lower()

    preenchimentos = {
        'input[placeholder*="Nome"], #nome': nome,
        'input[placeholder*="CEP"], #cep': dados.get("cep", ""),
        'input[placeholder*="ndereço"], #logradouro': dados.get("endereco", ""),
        'input[placeholder*="Cidade"], #cidade': dados.get("cidade", ""),
        'input[placeholder*="UF"], #uf, #estado': dados.get("uf", ""),
        'input[type="email"], input[placeholder*="mail"]': dados.get("email", f"{primeiro}@gmail.com"),
    }

    for seletor, valor in preenchimentos.items():
        if not valor:
            continue
        try:
            campo = await page.query_selector(seletor)
            if campo:
                await campo.fill(valor)
                if "cep" in seletor.lower():
                    await campo.press("Tab")
                    await page.wait_for_timeout(600)
        except Exception:
            pass

    await page.click('button:has-text("Salvar")')
    await page.wait_for_timeout(1000)


async def aguardar_lookup_sacado(page: Page, cnpj_limpo: str) -> bool:
    """Aguarda o lookup do sacado completar após Tab no campo saca_id.
    Retorna False se abriu o Cadastro de Sacado, True se o sacado foi encontrado."""
    for _ in range(40):
        await page.wait_for_timeout(100)
        cadastro = await page.query_selector('text=Cadastro de sacado')
        if cadastro:
            return False
        saca_val = await page.evaluate(
            "() => { const el = document.querySelector('#saca_id'); return el ? el.value : ''; }"
        )
        if saca_val and saca_val.replace('.', '').replace('/', '').replace('-', '') == cnpj_limpo:
            return True
    return True


async def preencher_titulo_fluxasset(page: Page, fatura: dict, status: dict):
    """Preenche o formulário de digitação da FluxAsset"""
    log = lambda msg: status["logs"].append(msg)
    cnpj_limpo = re.sub(r'\D', '', fatura["cliente_cnpj"])

    saca_locator = page.locator('#saca_id').first
    await saca_locator.wait_for(state="visible", timeout=8000)
    await saca_locator.fill(cnpj_limpo)
    await saca_locator.press('Tab')

    sacado_ok = await aguardar_lookup_sacado(page, cnpj_limpo)
    if not sacado_ok:
        await cadastrar_sacado_se_necessario(page, fatura, status)

    valor_fmt = f"{fatura['valor']:.2f}".replace(".", ",")
    for sel, val in [
        ('#data_titu', fatura["vencimento"]),
        ('#valo_titu', valor_fmt),
        ('#nume_doct', fatura["numero"]),
        ('#nume_nota', fatura["numero"]),
        ('#data_emis', fatura["emissao"]),
        ('#valo_nota', valor_fmt),
        ('#chave_nf',  fatura.get("chave", "")),
    ]:
        if val:
            try:
                await page.fill(sel, str(val))
                await page.wait_for_timeout(80)   # reduzido
            except Exception:
                pass

    await page.wait_for_timeout(300)   # reduzido de 500ms
    await page.evaluate("""
        () => {
            const btns = [...document.querySelectorAll('button')];
            const btn = btns.find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
            if (btn) btn.click();
        }
    """)
    await page.wait_for_timeout(900)   # reduzido de 1500ms
    log(f"  ✅ Título {fatura['numero']} - {fatura.get('cliente_nome', '')} salvo")


async def _aplicar_filtro_data_e_pesquisar(page, data_op: str):
    """Preenche o filtro de Período com data_op e clica Pesquisar."""
    await page.evaluate(f"""
        () => {{
            const data = '{data_op}';
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            const inputs = [...document.querySelectorAll('input[type="text"]')].slice(0, 2);
            inputs.forEach(inp => {{
                setter.call(inp, data);
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
            }});
        }}
    """)
    await page.wait_for_timeout(300)
    pesquisar = await page.query_selector('button:has-text("Pesquisar")')
    if pesquisar:
        await pesquisar.click()
        await page.wait_for_timeout(1500)


async def _localizar_linha_aguardando(page) -> object | None:
    try:
        await page.wait_for_selector("tr:has-text('Aguardando')", timeout=8000)
    except Exception:
        return None
    return await page.query_selector("tr:has-text('Aguardando')")


async def _finalizar_na_pagina(page, sistema: str, status: dict):
    """Finaliza a operação na página já aberta — sem abrir novo browser."""
    log = lambda msg: status["logs"].append(msg)
    data_op = _data_operacao_str()

    log(f"  📅 Filtrando operações pela data: {data_op}")
    await _aplicar_filtro_data_e_pesquisar(page, data_op)

    linha = await _localizar_linha_aguardando(page)
    if not linha:
        log("  ⚠️ Operação com status Aguardando não encontrada")
        return

    # Abre Ações → Definir conta corrente
    botao_acoes = await linha.query_selector('button:has-text("Ações"), .btn-acoes')
    await botao_acoes.click()
    await page.wait_for_timeout(400)
    await page.click("text=Definir conta corrente")
    log("  🏦 Selecionando conta corrente...")

    try:
        await page.wait_for_selector("text=Conta Corrente", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(700)

    # 1ª linha de dados da tabela (tr:nth-child(2) pula o cabeçalho)
    primeiro_btn = await page.query_selector(
        "table tr:nth-child(2) button, "
        "table tbody tr:first-child button, "
        "table tr:nth-child(2) .btn"
    )
    if primeiro_btn:
        await primeiro_btn.click()
        log("  ✅ Conta corrente definida")
    else:
        log("  ⚠️ Botão de conta não encontrado — continuando")

    await page.wait_for_timeout(1500)

    # Encaminhar — menu Ações permanece aberto após selecionar conta
    encaminhar = await page.query_selector("text=Encaminhar para operação / encerrar")
    if not encaminhar:
        linha2 = await _localizar_linha_aguardando(page)
        if linha2:
            botao_acoes2 = await linha2.query_selector('button:has-text("Ações"), .btn-acoes')
            if botao_acoes2:
                await botao_acoes2.click()
                await page.wait_for_timeout(400)

    await page.click("text=Encaminhar para operação / encerrar")
    await page.wait_for_timeout(1500)
    log(f"  ✅ Operação FluxAsset {sistema} encaminhada com sucesso!")


async def executar_fluxasset(faturas_selecao, sistema: str, status: dict) -> dict:
    """
    Executa a digitação completa na FluxAsset.
    Mantém browser visível (headless=True) para passar o Cloudflare Turnstile —
    clique manualmente em "Confirme que é humano" se aparecer.
    """
    log = lambda msg: status["logs"].append(msg)
    faturas_dados = status.get("faturas_cache", {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        log(f"🔐 Fazendo login na FluxAsset ({sistema})...")
        await fazer_login_fluxasset(page, sistema)

        log("📂 Navegando para Digitação...")
        await navegar_para_digitacao(page)

        await page.locator('button:has-text("Novo")').first.click()
        await page.wait_for_timeout(1000)   # reduzido de 1500ms

        await page.evaluate("""
            () => {
                const spans = [...document.querySelectorAll('li.aba-cabecalho-lista-li span')];
                const tab = spans.find(s => s.textContent.trim() === 'Digitação');
                if (tab) tab.closest('li').click();
            }
        """)
        await page.wait_for_timeout(600)   # reduzido de 800ms

        for idx, sel in enumerate(faturas_selecao):
            fatura = faturas_dados.get(sel.numero)
            if not fatura:
                log(f"  ⚠️ Dados não encontrados para fatura {sel.numero}")
                continue

            log(f"📝 [{idx+1}/{len(faturas_selecao)}] Digitando fatura {sel.numero} - {fatura.get('cliente_nome', '')}...")

            if idx > 0:
                await page.evaluate("""
                    () => {
                        const salvar = [...document.querySelectorAll('button')]
                            .find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
                        if (!salvar) return 'salvar_nao_encontrado';
                        let el = salvar;
                        for (let i = 0; i < 8; i++) {
                            el = el.parentElement;
                            if (!el) break;
                            const novo = [...el.querySelectorAll('button')]
                                .find(b => b.textContent.trim() === 'Novo' && b.offsetParent !== null);
                            if (novo) { novo.click(); return 'clicado_nivel_' + i; }
                        }
                        return 'novo_nao_encontrado';
                    }
                """)
                await page.wait_for_timeout(800)   # reduzido de 1500ms

                saca_vis = await page.evaluate("""
                    () => { const el = document.querySelector('#saca_id'); if(!el) return 'nao_existe';
                    return el.getBoundingClientRect().width > 0 ? 'visivel' : 'oculto'; }
                """)
                if saca_vis != 'visivel':
                    await page.evaluate("""
                        () => {
                            const spans = [...document.querySelectorAll('li.aba-cabecalho-lista-li span')];
                            const tab = spans.find(s => s.textContent.trim() === 'Digitação');
                            if (tab) tab.closest('li').click();
                        }
                    """)
                    await page.wait_for_timeout(500)   # reduzido de 800ms

            try:
                await preencher_titulo_fluxasset(page, fatura, status)
                status["concluidas"] += 1
                status.setdefault("faturas_salvas", set()).add(sel.numero)
            except Exception as e:
                log(f"  ❌ Erro na fatura {sel.numero}: {str(e)}")
                status["erros"].append(f"Fatura {sel.numero}: {str(e)}")

        # Fecha modal e recarrega para ver operação criada
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await page.reload()
        await page.wait_for_timeout(1500)

        data_op = _data_operacao_str()
        await _aplicar_filtro_data_e_pesquisar(page, data_op)

        await browser.close()

    return {"sistema": sistema}


# ── Mantido para compatibilidade, não abre novo browser ─────────────────────
async def finalizar_fluxasset(sistema: str, status: dict):
    """
    Legado — abre novo browser para finalizar.
    Preferível usar confirmacao_event em executar_fluxasset para evitar re-login.
    """
    log = lambda msg: status["logs"].append(msg)
    data_op = _data_operacao_str()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await fazer_login_fluxasset(page, sistema)
        await navegar_para_digitacao(page)
        log(f"  📅 Filtrando operações pela data: {data_op}")
        await _aplicar_filtro_data_e_pesquisar(page, data_op)
        await _finalizar_na_pagina(page, sistema, status)
        await browser.close()
