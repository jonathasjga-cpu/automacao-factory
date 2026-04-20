import asyncio
import re
import httpx
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page
from config_manager import get_credencial

def _data_operacao_str() -> str:
    """Retorna a data em que a Firma registra a operação (próxima segunda em fins de semana)."""
    hoje = datetime.now()
    if hoje.weekday() == 5:   # sábado
        return (hoje + timedelta(days=2)).strftime("%d/%m/%Y")
    elif hoje.weekday() == 6:  # domingo
        return (hoje + timedelta(days=1)).strftime("%d/%m/%Y")
    return hoje.strftime("%d/%m/%Y")

FIRMA_URL = "https://intrafac777.firmasa.com/Factadebentures"

async def buscar_dados_cnpj(cnpj: str) -> dict:
    cnpj_limpo = re.sub(r'\D', '', cnpj)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://publica.cnpj.ws/cnpj/{cnpj_limpo}")
            if resp.status_code == 200:
                dados = resp.json()
                razao = dados.get("razao_social", "")
                primeiro_nome = razao.split()[0].capitalize() if razao else "empresa"
                end = dados.get("estabelecimento", {})
                return {
                    "nome": razao,
                    "cep": re.sub(r'\D', '', end.get("cep", "")),
                    "endereco": end.get("logradouro", ""),
                    "numero": end.get("numero", ""),
                    "bairro": end.get("bairro", ""),
                    "cidade": end.get("cidade", {}).get("nome", "") if isinstance(end.get("cidade"), dict) else end.get("cidade", ""),
                    "uf": end.get("estado", {}).get("sigla", "") if isinstance(end.get("estado"), dict) else end.get("estado", ""),
                    "email": f"{primeiro_nome.lower()}@gmail.com",
                }
    except Exception:
        pass
    return {}

async def fazer_login_firma(page: Page, sistema: str):
    creds = get_credencial(sistema)
    await page.goto(f"{FIRMA_URL}/login")
    await page.wait_for_load_state("networkidle")
    await page.fill('input[placeholder*="suário"], input[name*="user"], input[type="text"]', creds["usuario"])
    await page.fill('input[type="password"]', creds["senha"])
    await page.click('button:has-text("Entrar"), button[type="submit"]')
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(800)

async def navegar_para_digitacao(page: Page):
    await page.evaluate(
        "() => { const a = document.querySelector('a[href*=\"/operacao/digitacao\"]'); if(a) a.click(); }"
    )
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)

async def aguardar_lookup_sacado(page: Page, cnpj_limpo: str) -> bool:
    for _ in range(40):
        await page.wait_for_timeout(100)
        cadastro = await page.query_selector('text=Cadastro de Sacado, text=Cadastro de sacado')
        if cadastro:
            return False
        saca_val = await page.evaluate("""
            () => {
                const el = document.querySelector('#saca_id');
                return el ? el.value : '';
            }
        """)
        if saca_val and saca_val.replace('.', '').replace('/', '').replace('-', '') == cnpj_limpo:
            return True
    return True

async def preencher_titulo(page: Page, fatura: dict, status: dict):
    log = lambda msg: status["logs"].append(msg)
    cnpj_limpo = re.sub(r'\D', '', fatura["cliente_cnpj"])

    saca_locator = page.locator('#saca_id').first
    await saca_locator.wait_for(state="visible", timeout=8000)
    await saca_locator.fill(cnpj_limpo)
    await saca_locator.press('Tab')

    sacado_ok = await aguardar_lookup_sacado(page, cnpj_limpo)

    if not sacado_ok:
        log(f"  📋 Cliente {cnpj_limpo} não cadastrado. Buscando na Receita Federal...")
        dados = await buscar_dados_cnpj(cnpj_limpo)
        nome_usar = dados.get("nome") or fatura["cliente_nome"]

        for sel, val in [
            ('#nome, input[placeholder*="Nome"]', nome_usar),
            ('#cep, input[placeholder*="CEP"]', dados.get("cep", "")),
        ]:
            campo = await page.query_selector(sel)
            if campo and val:
                await campo.fill(val)
                await campo.press("Tab")
                await page.wait_for_timeout(600)

        for sel, val in [
            ('#logradouro, input[placeholder*="ndere"]', dados.get("endereco", "")),
            ('#cidade, input[placeholder*="Cidade"]', dados.get("cidade", "")),
            ('#uf, #estado, input[placeholder*="UF"]', dados.get("uf", "")),
        ]:
            campo = await page.query_selector(sel)
            if campo and val:
                current = await campo.get_attribute("value") or ""
                if not current:
                    await campo.fill(val)

        email_campo = await page.query_selector('input[type="email"]')
        if email_campo:
            primeiro = nome_usar.split()[0].lower()
            await email_campo.fill(f"{primeiro}@gmail.com")

        await page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button')];
                const btn = btns.find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
                if (btn) btn.click();
            }
        """)
        try:
            await page.locator('text=Cadastro de Sacado, text=Cadastro de sacado').wait_for(state="hidden", timeout=8000)
        except Exception:
            await page.wait_for_timeout(1500)
        log(f"  ✅ Sacado cadastrado: {nome_usar}")

    valor_fmt = f"{fatura['valor']:.2f}".replace(".", ",")
    campos = [
        ('#data_titu', fatura["vencimento"]),
        ('#valo_titu', valor_fmt),
        ('#nume_doct', fatura["numero"]),
        ('#nume_nota', fatura["numero"]),
        ('#data_emis', fatura["emissao"]),
        ('#valo_nota', valor_fmt),
        ('#chave_nf',  fatura.get("chave", "")),
    ]
    for sel, val in campos:
        if val:
            try:
                await page.fill(sel, str(val))
                await page.wait_for_timeout(80)   # reduzido de 100ms → 80ms
            except Exception:
                pass

    await page.wait_for_timeout(300)   # reduzido de 500ms → 300ms

    await page.evaluate("""
        () => {
            const btns = [...document.querySelectorAll('button')];
            const btn = btns.find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
            if (btn) btn.click();
        }
    """)
    await page.wait_for_timeout(900)   # reduzido de 1500ms → 900ms
    log(f"  ✅ Título {fatura['numero']} - {fatura.get('cliente_nome', '')} salvo")


async def _aplicar_filtro_data_e_pesquisar(page, data_op: str):
    """Preenche o filtro de Período com data_op (início e fim) e clica Pesquisar."""
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

    # Encaminhar — o menu Ações permanece aberto após selecionar a conta
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
    log(f"  ✅ Operação {sistema} encaminhada com sucesso!")


async def executar_firma(faturas_selecao, sistema: str, status: dict) -> dict:
    """Executa digitação na Firma (headless — sem janela visível)."""
    log = lambda msg: status["logs"].append(msg)
    faturas_dados = status.get("faturas_cache", {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()

        log(f"🔐 Fazendo login na Firma ({sistema})...")
        await fazer_login_firma(page, sistema)

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
                novo_clicado = await page.evaluate("""
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
                await page.wait_for_timeout(800)   # reduzido de 1500ms → 800ms

                saca_visivel = await page.evaluate("""
                    () => {
                        const el = document.querySelector('#saca_id');
                        if (!el) return 'nao_existe';
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 ? 'visivel' : 'oculto';
                    }
                """)

                if saca_visivel != 'visivel':
                    await page.evaluate("""
                        () => {
                            const spans = [...document.querySelectorAll('li.aba-cabecalho-lista-li span')];
                            const tab = spans.find(s => s.textContent.trim() === 'Digitação');
                            if (tab) tab.closest('li').click();
                        }
                    """)
                    await page.wait_for_timeout(500)   # reduzido de 800ms

            try:
                await preencher_titulo(page, fatura, status)
                status["concluidas"] += 1
                status.setdefault("faturas_salvas", set()).add(sel.numero)
            except Exception as e:
                log(f"  ❌ Erro na fatura {sel.numero}: {str(e)}")
                status["erros"].append(f"Fatura {sel.numero}: {str(e)}")

        # Fecha modal e recarrega para ver a lista de operações
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
async def finalizar_firma(sistema: str, sequencial, status: dict):
    """
    Legado — abre novo browser para finalizar.
    Preferível usar confirmacao_event em executar_firma para evitar re-login.
    """
    log = lambda msg: status["logs"].append(msg)
    data_op = _data_operacao_str()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        page = await browser.new_page()
        await fazer_login_firma(page, sistema)
        await navegar_para_digitacao(page)
        log(f"  📅 Filtrando operações pela data: {data_op}")
        await _aplicar_filtro_data_e_pesquisar(page, data_op)
        await _finalizar_na_pagina(page, sistema, status)
        await browser.close()
