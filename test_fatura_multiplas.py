"""
Teste: simula exatamente o que baixar_faturas_pdf() faz para 2 factories em sequência.
Verifica se na 2ª rodada o Pesquisar atualiza os resultados corretamente.

Executa: .venv\Scripts\python.exe test_fatura_multiplas.py
"""
import asyncio
import re
from datetime import datetime
from playwright.async_api import async_playwright
from backend.config_manager import get_credencial

BASE_GW = "https://webtrans.saas.gwsistemas.com.br"

def hoje():
    return datetime.now().strftime("%d/%m/%Y")

def ano_atual():
    return str(datetime.now().year)


async def fazer_busca(page, rodada: int, filial_label: str):
    """Replica exatamente o que baixar_faturas_pdf() faz a cada iteração."""
    print(f"\n{'='*60}")
    print(f"  RODADA {rodada} — filial={filial_label}")
    print(f"{'='*60}")

    # 1. Navega para acao=iniciar
    print("  [1] Navegando para consultafatura?acao=iniciar...")
    await page.goto(f"{BASE_GW}/consultafatura?acao=iniciar", wait_until="domcontentloaded", timeout=30000)
    await page.locator('select[name="campoDeConsulta"]').wait_for(state="visible", timeout=15000)
    print(f"  URL após goto: {page.url}")

    # 2. Preenche filtros
    hoje_str = hoje()
    print(f"  [2] Preenchendo filtros: data={hoje_str}, filial={filial_label}")
    await page.select_option('select[name="campoDeConsulta"]', value="emissao_fatura")
    await page.fill('input[name="dtemissao1"]', hoje_str)
    await page.fill('input[name="dtemissao2"]', hoje_str)
    await page.select_option('select[name="filialId"]', label=filial_label)
    await page.select_option('select[name="finalizada"]', label="Todas")

    # 3. Clica Pesquisar
    print("  [3] Clicando em Pesquisar...")
    await page.click('input[value="Pesquisar"]')
    await page.wait_for_load_state("load", timeout=30000)

    # 4. Aguarda resultados
    ano_str = f"/{ano_atual()}"
    print(f"  [4] Aguardando '{ano_str}' aparecer no body...")
    try:
        await page.wait_for_function(
            f"() => document.body.innerText.includes('{ano_str}')",
            timeout=15000
        )
        print("       OK — resultados carregaram")
    except Exception:
        print("       TIMEOUT — resultados NÃO apareceram em 15s")
    await page.wait_for_timeout(800)
    print(f"  URL após pesquisa: {page.url}")

    # 5. Lista faturas encontradas
    na_pagina = []
    for tr in await page.query_selector_all("tr"):
        try:
            texto = await tr.inner_text()
            m = re.search(r"(\d{5,6})/\d{4}", texto)
            if m:
                na_pagina.append(m.group(1))
        except Exception:
            pass
    print(f"  [5] Faturas na página: {na_pagina}")

    # 6. Lista checkboxes ck*
    all_cbs = await page.query_selector_all('input[id^="ck"]')
    print(f"  [6] Checkboxes input[id^=ck]: {len(all_cbs)}")
    for cb in all_cbs:
        cb_id = await cb.get_attribute("id") or ""
        if not re.match(r'^ck\d+$', cb_id):
            print(f"       #{cb_id} — ignorado (não é ckN)")
            continue
        tr_text = await cb.evaluate("""el => {
            let e = el.parentElement;
            for (let i = 0; i < 8; i++) {
                if (!e) return '(sem TR)';
                if (e.tagName === 'TR') return e.innerText.replace(/\\n/g, ' | ').substring(0, 100);
                e = e.parentElement;
            }
            return '(TR nao encontrado)';
        }""")
        print(f"       #{cb_id} -> {tr_text}")

    # 7. Tenta marcar TODOS os ck*
    print("  [7] Tentando marcar todos os ck* via page.locator()...")
    marcados = 0
    for cb in all_cbs:
        cb_id = await cb.get_attribute("id") or ""
        if not re.match(r'^ck\d+$', cb_id):
            continue
        try:
            await page.locator(f'#{cb_id}').check()
            checked = await page.locator(f'#{cb_id}').is_checked()
            print(f"       #{cb_id} -> checked={checked}")
            if checked:
                marcados += 1
        except Exception as e:
            print(f"       #{cb_id} -> ERRO: {e}")

    print(f"  [7] Total marcados: {marcados}/{len([c for c in all_cbs])}")
    return na_pagina


async def main():
    creds = get_credencial("gw")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        context = await browser.new_context()
        page = await context.new_page()

        # Login
        print("Fazendo login...")
        await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded")
        await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
        await page.locator('input[name="login"]').fill(creds["usuario"])
        await page.locator('input[name="senha"]').fill(creds["senha"])
        await page.locator('button.button-login').click()
        # Aguarda redirect para fora do login
        try:
            await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        except Exception:
            pass
        # Navega explicitamente para /home para garantir sessão inicializada
        if "/home" not in page.url:
            await page.goto(f"{BASE_GW}/home", wait_until="load", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        print(f"Logado. URL: {page.url}")

        # Rodada 1 — MATRIZ (simula factory 1)
        await fazer_busca(page, rodada=1, filial_label="MATRIZ")

        # Rodada 2 — MATRIZ novamente (simula factory 2 com mesma filial)
        await fazer_busca(page, rodada=2, filial_label="MATRIZ")

        await page.screenshot(path="C:/Temp/test_fatura_multiplas.png", full_page=False)
        print("\nScreenshot: C:/Temp/test_fatura_multiplas.png")

        input("\nPressione Enter para fechar...")
        await browser.close()


asyncio.run(main())
