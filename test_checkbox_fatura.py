"""
Teste isolado: diagnóstico de checkboxes na página de faturas do GW.
Executa: .venv\Scripts\python.exe test_checkbox_fatura.py
"""
import asyncio
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
        page    = await context.new_page()

        # ── Login ──────────────────────────────────────────────────────
        print("Fazendo login...")
        await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded")
        await page.locator('input[name="login"]').fill(creds["usuario"])
        await page.locator('input[name="senha"]').fill(creds["senha"])
        await page.locator('button.button-login').click()
        await page.wait_for_url(lambda u: "login" not in u, timeout=30000)
        await page.goto(f"{BASE_GW}/home", wait_until="load")
        await page.wait_for_timeout(2000)
        print(f"  Logado. URL: {page.url}")

        # ── Navegação ──────────────────────────────────────────────────
        print("\nAcessando consultafatura...")
        await page.goto(f"{BASE_GW}/consultafatura?acao=iniciar", wait_until="load")
        await page.locator('select[name="campoDeConsulta"]').wait_for(state="visible", timeout=15000)

        # ── Filtros ────────────────────────────────────────────────────
        hoje_str = hoje()
        print(f"  Filtro: Data de Emissão = {hoje_str}, Filial = MATRIZ")
        await page.select_option('select[name="campoDeConsulta"]', value="emissao_fatura")
        await page.fill('input[name="dtemissao1"]', hoje_str)
        await page.fill('input[name="dtemissao2"]', hoje_str)
        await page.select_option('select[name="filialId"]', label="MATRIZ")
        await page.select_option('select[name="finalizada"]',  label="Todas")
        await page.click('input[value="Pesquisar"]')
        await page.wait_for_load_state("load", timeout=30000)
        # Aguarda pelo menos uma <tr> com número de fatura aparecer
        try:
            await page.wait_for_function(
                "() => document.body.innerText.includes('/2026')",
                timeout=15000
            )
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        print(f"  URL após pesquisa: {page.url}")

        # ── Diagnóstico: todos os checkboxes da página ─────────────────
        print("\n=== TODOS OS CHECKBOXES NA PÁGINA ===")
        all_inputs = await page.query_selector_all('input[type="checkbox"]')
        print(f"Total input[type=checkbox]: {len(all_inputs)}")
        for inp in all_inputs:
            id_   = await inp.get_attribute("id")   or "(sem id)"
            name_ = await inp.get_attribute("name") or "(sem name)"
            chk   = await inp.is_checked()
            vis   = await inp.is_visible()
            print(f"  id={id_:<15} name={name_:<20} checked={chk} visible={vis}")

        # ── Diagnóstico: checkboxes ck* ────────────────────────────────
        print("\n=== CHECKBOXES id^=ck ===")
        ck_inputs = await page.query_selector_all('input[id^="ck"]')
        print(f"Total input[id^=ck]: {len(ck_inputs)}")
        for cb in ck_inputs:
            cb_id = await cb.get_attribute("id") or ""
            # Texto do <tr> pai
            tr_text = await cb.evaluate("""el => {
                let e = el.parentElement;
                for (let i = 0; i < 8; i++) {
                    if (!e) return '(sem TR)';
                    if (e.tagName === 'TR') return e.innerText.replace(/\\n/g, ' | ').substring(0, 120);
                    e = e.parentElement;
                }
                return '(TR nao encontrado)';
            }""")
            print(f"  #{cb_id:<12} TR: {tr_text}")

        # ── Tenta marcar ck0 ──────────────────────────────────────────
        print("\n=== TENTANDO MARCAR PRIMEIRO ck* ===")
        if ck_inputs:
            first = ck_inputs[0]
            first_id = await first.get_attribute("id")
            print(f"  Tentando .check() em #{first_id}...")
            try:
                await first.check()
                await page.wait_for_timeout(500)
                checked = await first.is_checked()
                print(f"  Resultado: checked={checked}")
            except Exception as e:
                print(f"  Erro no .check(): {e}")
        else:
            print("  Nenhum checkbox ck* encontrado!")

        # ── Screenshot ────────────────────────────────────────────────
        await page.screenshot(path="C:/Temp/test_checkbox_result.png", full_page=False)
        print("\nScreenshot salvo em C:/Temp/test_checkbox_result.png")

        input("\nPressione Enter para fechar o browser...")
        await browser.close()


asyncio.run(main())
