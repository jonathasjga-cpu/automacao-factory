import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from playwright.async_api import async_playwright, Page
from config_manager import get_credencial

GC_URL = "http://gcrecursos.dyndns.org:9000/FactaConsult"
GW_REMESSA_URL = "https://webtrans.saas.gwsistemas.com.br/jspexporta_boleto.jsp"

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "automacao_factory"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Conta bancária por filial
CONTA_POR_FILIAL = {
    "gc_matriz": "3196-8",
    "gc_sp": "03196-8",
}

# ─── ETAPA 1: GW — Gerar arquivo .rem ────────────────────────────────────────

async def gerar_remessa_gw(numeros_fatura: list[str], sistema: str, status: dict) -> Path:
    """
    Acessa GW > Processos > Financeiro > Gerar Arquivo de Remessa,
    filtra por hoje, marca apenas as faturas selecionadas e baixa o .rem.
    """
    log = lambda msg: status["logs"].append(msg)
    creds_gw = get_credencial("gw")
    conta = CONTA_POR_FILIAL[sistema]
    hoje = _hoje()

    log(f"  📥 Gerando remessa no GW para conta {conta}...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        # Login GW
        await page.goto("https://webtrans.saas.gwsistemas.com.br/login")
        await page.wait_for_load_state("networkidle")
        await page.fill('input[name="login"], input[type="text"]', creds_gw["usuario"])
        await page.fill('input[name="senha"], input[type="password"]', creds_gw["senha"])
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # Processos → Financeiro → Gerar arquivo de remessa
        await page.click("text=Processos")
        await page.wait_for_timeout(300)
        await page.click("text=Financeiro")
        await page.wait_for_timeout(300)
        await page.click("text=Gerar arquivo de remessa")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1000)

        # Pesquisar por: Data de Emissão
        campo_tipo = await page.query_selector('select[name*="campoDeConsulta"], select:first-of-type')
        if campo_tipo:
            await campo_tipo.select_option(label="Data de Emissão")

        # De / Até = hoje
        for seletor in ['input[name*="dtemissao1"]', 'input[name*="De"]']:
            try:
                campo = await page.query_selector(seletor)
                if campo:
                    await campo.fill(hoje)
                    break
            except Exception:
                pass

        for seletor in ['input[name*="dtemissao2"]', 'input[name*="Ate"]']:
            try:
                campo = await page.query_selector(seletor)
                if campo:
                    await campo.fill(hoje)
                    break
            except Exception:
                pass

        # Seleciona conta correta
        campo_conta = await page.query_selector('select[name*="idConta"], select[name*="Conta"]')
        if campo_conta:
            await campo_conta.select_option(label=conta)
            log(f"  ✅ Conta selecionada: {conta}")

        # Pesquisar
        await page.click('input[value="Pesquisar"], button:has-text("Pesquisar")')
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1000)

        # Marca apenas as faturas selecionadas pelo usuário
        marcadas = 0
        linhas = await page.query_selector_all("table tr")
        for linha in linhas:
            celulas = await linha.query_selector_all("td")
            if not celulas:
                continue
            try:
                texto_fatura = await celulas[0].inner_text()
                # Extrai número antes do "/" → "004453/2026" → "004453"
                num = texto_fatura.strip().split("/")[0].strip().zfill(6)
                if num in numeros_fatura:
                    checkbox = await linha.query_selector('input[type="checkbox"]')
                    if checkbox:
                        await checkbox.check()
                        marcadas += 1
            except Exception:
                continue

        log(f"  ✅ {marcadas} fatura(s) marcada(s) para remessa")

        if marcadas == 0:
            log("  ⚠️ Nenhuma fatura encontrada na tela de remessa")
            await browser.close()
            return None

        # Baixa o arquivo .rem
        nome_arquivo = f"remessa_{sistema}_{hoje.replace('/', '')}.rem"
        caminho = DOWNLOAD_DIR / nome_arquivo

        async with page.expect_download() as dl:
            await page.click('input[value="Exportar Boletos"], button:has-text("Exportar Boletos")')

        download = await dl.value
        await download.save_as(str(caminho))
        log(f"  📁 Arquivo .rem salvo: {nome_arquivo}")

        await browser.close()
        return caminho

# ─── ETAPA 2: GC — Importar e preencher ──────────────────────────────────────

async def fazer_login_gc(page: Page, sistema: str):
    """Login na GC"""
    creds = get_credencial(sistema)
    await page.goto(f"{GC_URL}/login")
    await page.wait_for_load_state("networkidle")
    await page.fill('input[type="text"], input[placeholder*="suário"]', creds["usuario"])
    await page.fill('input[type="password"]', creds["senha"])
    await page.click('button:has-text("Entrar"), button[type="submit"]')
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1000)

async def importar_remessa_gc(page: Page, caminho_rem: Path, status: dict):
    """Importa o arquivo .rem na GC via Importar Layout"""
    log = lambda msg: status["logs"].append(msg)

    # Operação → Digitação
    await page.click("text=Operação")
    await page.wait_for_timeout(300)
    await page.click("text=Digitação")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(800)

    # Novo
    await page.click('button:has-text("Novo")')
    await page.wait_for_timeout(1000)

    # Importar Layout
    await page.click('button:has-text("Importar Leiaute"), button:has-text("Importar Layout")')
    await page.wait_for_timeout(800)

    # Upload do arquivo .rem
    input_file = await page.query_selector('input[type="file"]')
    if input_file:
        await input_file.set_input_files(str(caminho_rem))
        await page.wait_for_timeout(500)
    else:
        log("  ⚠️ Campo de upload não encontrado")
        return False

    # Enviar
    await page.click('button:has-text("Enviar")')
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)
    log("  ✅ Arquivo .rem importado com sucesso")
    return True

async def preencher_num_nota_gc(page: Page, faturas_dados: dict, status: dict):
    """
    Para cada fatura importada, abre pelo lápis e preenche Núm.Nota
    com o valor do campo Documento. Pagina de 30 em 30.
    """
    log = lambda msg: status["logs"].append(msg)

    # Atualiza página para ver operação criada
    await page.reload()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)

    # Abre a operação com status Aguardando pela lupa
    lupa = await page.query_selector("tr:has-text('Aguardando') button.btn-sm, tr:has-text('Aguardando') .fa-search")
    if not lupa:
        lupa = await page.query_selector("table tbody tr:first-child button:first-child")
    if lupa:
        await lupa.click()
        await page.wait_for_timeout(1000)

    total_preenchidos = 0

    while True:
        linhas = await page.query_selector_all("table tbody tr")
        linhas_validas = [l for l in linhas if await l.query_selector("td")]

        for linha in linhas_validas:
            try:
                celulas = await linha.query_selector_all("td")
                doc = ""
                for cell in celulas:
                    txt = (await cell.inner_text()).strip()
                    if re.match(r'^\d{6}$', txt):
                        doc = txt
                        break

                if not doc:
                    continue

                lapis = await linha.query_selector('button .fa-pencil, button[title*="ditar"], .btn-warning')
                if not lapis:
                    lapis = await linha.query_selector("button:nth-child(1)")
                if lapis:
                    await lapis.click()
                    await page.wait_for_timeout(600)

                campo_num_nota = await page.query_selector(
                    'input[placeholder*="úm"], #numNota, input[id*="numNota"]'
                )
                if campo_num_nota:
                    await campo_num_nota.fill(doc)

                await page.click('button:has-text("Salvar")')
                await page.wait_for_timeout(500)
                total_preenchidos += 1
                log(f"  ✅ Núm.Nota preenchido: {doc}")

            except Exception as e:
                log(f"  ⚠️ Erro ao preencher linha: {str(e)}")
                continue

        btn_proxima = await page.query_selector('button[title="Próxima"], .fa-chevron-right:not(.disabled)')
        if btn_proxima:
            desabilitado = await btn_proxima.get_attribute("disabled")
            if desabilitado:
                break
            await btn_proxima.click()
            await page.wait_for_timeout(800)
        else:
            break

    log(f"  ✅ Total de Núm.Nota preenchidos: {total_preenchidos}")
    return total_preenchidos

async def finalizar_gc(page: Page, sistema: str, status: dict):
    """Ações → Definir conta corrente → Encaminhar para operação"""
    log = lambda msg: status["logs"].append(msg)

    # Volta para listagem de digitações
    await page.reload()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)

    linha_aguardando = await page.query_selector("tr:has-text('Aguardando')")
    if not linha_aguardando:
        log("  ⚠️ Operação Aguardando não encontrada")
        return

    # Ações → Definir conta corrente
    botao_acoes = await linha_aguardando.query_selector('button:has-text("Ações")')
    await botao_acoes.click()
    await page.wait_for_timeout(400)
    await page.click("text=Definir conta corrente")
    await page.wait_for_timeout(1000)
    log("  🏦 Selecionando conta corrente...")

    primeiro_btn = await page.query_selector(
        "table tr:nth-child(2) button, table tr:nth-child(2) .btn-primary"
    )
    if primeiro_btn:
        await primeiro_btn.click()
        await page.wait_for_timeout(800)

    # Ações → Encaminhar
    linha_aguardando = await page.query_selector("tr:has-text('Aguardando')")
    if linha_aguardando:
        botao_acoes2 = await linha_aguardando.query_selector('button:has-text("Ações")')
        if botao_acoes2:
            await botao_acoes2.click()
            await page.wait_for_timeout(400)
    await page.click("text=Encaminhar para operação / encerrar")
    await page.wait_for_timeout(1500)
    log(f"  ✅ GC {sistema} encaminhada com sucesso!")

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

async def executar_gc(faturas_selecao, sistema: str, status: dict) -> dict:
    """Executa o fluxo completo da GC: gera .rem no GW e importa na GC (headless)."""
    log = lambda msg: status["logs"].append(msg)

    faturas_dados = status.get("faturas_cache", {})
    numeros = [sel.numero for sel in faturas_selecao]

    total_valor = sum(faturas_dados[n]["valor"] for n in numeros if n in faturas_dados)
    total_qtd = len(numeros)

    log(f"📋 GC {sistema}: {total_qtd} fatura(s) | Total: R$ {total_valor:,.2f}")

    # Etapa 1: gera remessa no GW (browser separado, headless)
    caminho_rem = await gerar_remessa_gw(numeros, sistema, status)
    if not caminho_rem:
        status["erros"].append(f"GC {sistema}: falha ao gerar arquivo de remessa")
        return {}

    # Etapa 2: importa na GC, preenche, e (se confirmado) finaliza no mesmo browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        log(f"🔐 Fazendo login na GC ({sistema})...")
        await fazer_login_gc(page, sistema)

        log("📂 Importando arquivo de remessa na GC...")
        sucesso = await importar_remessa_gc(page, caminho_rem, status)

        if sucesso:
            log("📝 Preenchendo Núm.Nota em cada título...")
            await preencher_num_nota_gc(page, faturas_dados, status)

            status[f"{sistema}_pronto"] = True
            status[f"{sistema}_resumo"] = {"qtd": total_qtd, "valor": total_valor}
            status["concluidas"] += total_qtd
            for n in numeros:
                status.setdefault("faturas_salvas", set()).add(n)

        await browser.close()

    return {"sistema": sistema, "qtd": total_qtd, "valor": total_valor}


async def finalizar_gc_completo(sistema: str, status: dict):
    """Legado — abre novo browser para finalizar. Preferível usar confirmacao_event."""
    log = lambda msg: status["logs"].append(msg)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await fazer_login_gc(page, sistema)

        await page.click("text=Operação")
        await page.wait_for_timeout(300)
        await page.click("text=Digitação")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)

        await finalizar_gc(page, sistema, status)
        await browser.close()

# ─── UTILS ───────────────────────────────────────────────────────────────────

def _hoje() -> str:
    return datetime.now().strftime("%d/%m/%Y")
