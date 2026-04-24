"""
Diagnóstico completo do processo de busca de faturas no GW.
Verifica: preenchimento do form, URL resultante, checkboxes.

Executa: .venv\Scripts\python.exe test_fatura_diag.py
"""
import asyncio
import re
from datetime import datetime
from playwright.async_api import async_playwright
from backend.config_manager import get_credencial

BASE_GW = "https://webtrans.saas.gwsistemas.com.br"

def hoje():
    return datetime.now().strftime("%d/%m/%Y")


async def main():
    creds = get_credencial("gw")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        context = await browser.new_context()
        page = await context.new_page()

        # ── Login ──────────────────────────────────────────────────────────────
        print("Fazendo login...")
        await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded")
        await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
        await page.locator('input[name="login"]').fill(creds["usuario"])
        await page.locator('input[name="senha"]').fill(creds["senha"])
        await page.locator('button.button-login').click()
        try:
            await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        except Exception:
            pass
        if "/home" not in page.url:
            await page.goto(f"{BASE_GW}/home", wait_until="load", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        print(f"Logado. URL: {page.url}\n")

        # ── Navega para o form ─────────────────────────────────────────────────
        print("[1] Navegando para consultafatura?acao=iniciar ...")
        await page.goto(f"{BASE_GW}/consultafatura?acao=iniciar",
                        wait_until="domcontentloaded", timeout=30000)
        await page.locator('select[name="campoDeConsulta"]').wait_for(state="visible", timeout=15000)
        print(f"    URL: {page.url}")
        print(f"    Titulo: {await page.title()}")

        # ── Inspeciona opções do select de filial ──────────────────────────────
        print("\n[2] Opcoes do select filialId:")
        opcoes = await page.evaluate("""() => {
            const s = document.querySelector('select[name="filialId"]');
            if (!s) return [];
            return [...s.options].map(o => ({value: o.value, text: o.text.trim(), selected: o.selected}));
        }""")
        for o in opcoes:
            print(f"    value='{o['value']}' text='{o['text']}' selected={o['selected']}")

        # ── Inspeciona opções do campoDeConsulta ───────────────────────────────
        print("\n[3] Opcoes do select campoDeConsulta:")
        opcoes_campo = await page.evaluate("""() => {
            const s = document.querySelector('select[name="campoDeConsulta"]');
            if (!s) return [];
            return [...s.options].map(o => ({value: o.value, text: o.text.trim(), selected: o.selected}));
        }""")
        for o in opcoes_campo:
            print(f"    value='{o['value']}' text='{o['text']}' selected={o['selected']}")

        # ── Preenche o form e loga os valores após cada passo ──────────────────
        hoje_str = hoje()
        print(f"\n[4] Preenchendo form com data={hoje_str}, filialId=1 ...")

        await page.select_option('select[name="campoDeConsulta"]', value="emissao_fatura")
        val_campo = await page.evaluate("() => document.querySelector('select[name=\"campoDeConsulta\"]').value")
        print(f"    campoDeConsulta apos select: '{val_campo}'")

        # Verifica se dtemissao1 existe
        dt1_existe = await page.evaluate("() => !!document.querySelector('input[name=\"dtemissao1\"]')")
        print(f"    input[name=dtemissao1] existe: {dt1_existe}")
        if dt1_existe:
            await page.fill('input[name="dtemissao1"]', hoje_str)
            await page.fill('input[name="dtemissao2"]', hoje_str)
            val_dt1 = await page.evaluate("() => document.querySelector('input[name=\"dtemissao1\"]').value")
            val_dt2 = await page.evaluate("() => document.querySelector('input[name=\"dtemissao2\"]').value")
            print(f"    dtemissao1 apos fill: '{val_dt1}'")
            print(f"    dtemissao2 apos fill: '{val_dt2}'")

        await page.select_option('select[name="filialId"]', value="1")
        val_filial = await page.evaluate("() => document.querySelector('select[name=\"filialId\"]').value")
        print(f"    filialId apos select: '{val_filial}'")

        await page.select_option('select[name="finalizada"]', label="Todas")
        val_final = await page.evaluate("() => document.querySelector('select[name=\"finalizada\"]').value")
        print(f"    finalizada apos select: '{val_final}'")

        # ── Clica Pesquisar e aguarda URL mudar ────────────────────────────────
        print("\n[5] Clicando Pesquisar ...")
        await page.click('input[value="Pesquisar"]')
        try:
            await page.wait_for_url(lambda u: "acao=consultar" in u, timeout=30000)
            print(f"    wait_for_url OK")
        except Exception as e:
            print(f"    wait_for_url TIMEOUT: {e}")
        print(f"    URL apos pesquisar: {page.url}")

        # ── Aguarda checkboxes ─────────────────────────────────────────────────
        print("\n[6] Aguardando input[id^=ck] (15s)...")
        try:
            await page.wait_for_selector('input[id^="ck"]', state="visible", timeout=15000)
            print("    Checkboxes apareceram!")
        except Exception:
            print("    TIMEOUT - nenhum checkbox apareceu em 15s")
        await page.wait_for_timeout(500)

        # ── Lista faturas e checkboxes na pagina ───────────────────────────────
        print("\n[7] Faturas encontradas nas linhas da tabela:")
        na_pagina = []
        for tr in await page.query_selector_all("tr"):
            try:
                texto = await tr.inner_text()
                m = re.search(r"(\d{5,6})/\d{4}", texto)
                if m:
                    na_pagina.append(m.group(1))
            except Exception:
                pass
        print(f"    {na_pagina}")

        print("\n[8] Checkboxes input[id^=ck]:")
        cbs = await page.query_selector_all('input[id^="ck"]')
        print(f"    Total: {len(cbs)}")
        for cb in cbs:
            cb_id = await cb.get_attribute("id") or ""
            vis = await cb.is_visible()
            print(f"    #{cb_id} visible={vis}")

        # ── Inspeciona o HTML da tabela de resultados ──────────────────────────
        print("\n[9] HTML da primeira linha de resultado (se existir):")
        primeira_tr = await page.query_selector("table tr:nth-child(2)")
        if primeira_tr:
            html = await primeira_tr.inner_html()
            print(f"    {html[:400]}")
        else:
            print("    Nenhuma <tr> encontrada")

        # ── Screenshot ────────────────────────────────────────────────────────
        await page.screenshot(path="C:/Temp/test_fatura_diag.png", full_page=False)
        print("\nScreenshot: C:/Temp/test_fatura_diag.png")

        input("\nPressione Enter para fechar...")
        await browser.close()


asyncio.run(main())
