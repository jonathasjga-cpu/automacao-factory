"""
Teste GC AO VIVO - abre Chrome visivel e executa todo o fluxo da GC.
Voce ve: login GW, lista de faturas, marca uma, exporta .rem, login GC,
import do .rem, preenchimento Num.Nota.
"""
import asyncio, sys, os, tempfile
sys.path.insert(0, "backend")
os.environ["PYTHONIOENCODING"] = "utf-8"

from datetime import datetime
from playwright.async_api import async_playwright
from config_manager import get_credencial
from pathlib import Path

BASE_GW = "https://webtrans.saas.gwsistemas.com.br"
GC_URL  = "http://gcrecursos.dyndns.org:9000/FactaConsult"


async def main():
    creds_gw = get_credencial("gw", user_id=1)
    creds_gc = get_credencial("gc_matriz")
    hoje = datetime.now().strftime("%d/%m/%Y")

    print(f"GW: {creds_gw['usuario']}")
    print(f"GC: {creds_gc['usuario']}")
    print(f"Hoje: {hoje}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page()

        # 1. LOGIN GW
        print("\n[1] Login GW...")
        await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded", timeout=30000)
        await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
        await page.fill('input[name="login"]', creds_gw["usuario"])
        await page.fill('input[name="senha"]', creds_gw["senha"])
        await page.locator('button.button-login').click()
        try:
            await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        print("    OK Login GW")

        # 2. /jspexporta_boleto.jsp
        print("\n[2] Abrindo Exportar Boletos...")
        await page.goto(f"{BASE_GW}/jspexporta_boleto.jsp", wait_until="load")
        await page.wait_for_timeout(2000)

        try: await page.select_option('select[name="campoDeConsulta"]', label="Data de Emissao")
        except:
            try: await page.select_option('select[name="campoDeConsulta"]', value="emissao_fatura")
            except: pass
        await page.fill('input[name="dtemissao1"]', hoje)
        await page.fill('input[name="dtemissao2"]', hoje)

        opt_info = await page.evaluate("""() => {
            const s = document.querySelector('select[name=\\"conta\\"]');
            if (!s) return null;
            const re = /^3196-8(\\s|\\/|-|$)/;
            const opt = [...s.options].find(o => re.test(o.text.trim()));
            return opt ? {value: opt.value, text: opt.text} : null;
        }""")
        if opt_info:
            await page.select_option('select[name="conta"]', value=opt_info["value"])
            print(f"    Conta: {opt_info['text']}")

        try:
            await page.select_option('select[name="tipoGerado"]', label="gerados / nao gerados")
        except:
            try:
                await page.select_option('select[name="tipoGerado"]', label="gerados / não gerados")
            except: pass

        await page.click('input[name="pesquisar"]')
        await page.wait_for_timeout(2500)

        faturas = await page.evaluate("""() => {
            const out = [];
            for (const tr of document.querySelectorAll('table tr')) {
                const tds = tr.querySelectorAll('td');
                if (tds.length < 2) continue;
                const t = (tds[1].textContent || '').trim();
                const m = t.match(/^(\\d{5,6})\\/(\\d{4})$/);
                if (m) out.push(m[1].padStart(6, '0'));
            }
            return out;
        }""")
        print(f"\n[3] Faturas Matriz disponiveis hoje: {len(faturas)}")
        if not faturas:
            print("    !! Nenhuma fatura - abortando")
            await page.wait_for_timeout(8000)
            await browser.close()
            return
        print(f"    Amostra: {faturas[:5]}")
        primeira = faturas[0]
        print(f"\n    >> Vou usar a PRIMEIRA: {primeira}")

        # 3. Marca a primeira
        await page.evaluate("""(num) => {
            for (const tr of document.querySelectorAll('table tr')) {
                const tds = tr.querySelectorAll('td');
                if (tds.length < 2) continue;
                if (tds[1].textContent.trim().startsWith(num)) {
                    const cb = tr.querySelector('input[type=\\"checkbox\\"]');
                    if (cb) cb.click();
                    return;
                }
            }
        }""", primeira)
        print(f"\n[4] Marquei fatura {primeira}. Exportando .rem...")

        DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "automacao_factory"
        DOWNLOAD_DIR.mkdir(exist_ok=True)
        nome = f"remessa_AO_VIVO_{datetime.now().strftime('%H%M%S')}.rem"
        caminho = DOWNLOAD_DIR / nome
        async with page.expect_download(timeout=30000) as dl:
            await page.click('input[value="Exportar Boletos"]')
        download = await dl.value
        await download.save_as(str(caminho))
        print(f"    OK .rem salvo: {caminho}")

        # 4. NOVO BROWSER PRA GC
        print("\n[5] Abrindo GC Recursos...")
        page_gc = await browser.new_page()
        await page_gc.goto(f"{GC_URL}/login", wait_until="domcontentloaded", timeout=90000)
        await page_gc.locator('#Email').wait_for(state="visible", timeout=15000)
        await page_gc.fill('#Email', creds_gc["usuario"])
        await page_gc.fill('#Password', creds_gc["senha"])
        await page_gc.locator('#btnEntrar').click()
        try:
            await page_gc.wait_for_url(lambda u: "login" not in u.lower(), timeout=20000)
        except: pass
        await page_gc.wait_for_timeout(2500)
        print("    OK Login GC")

        await page_gc.evaluate("""() => {
            const a = document.querySelector('a[href*=\\"/operacao/digitacao\\"]');
            if (a) a.click();
        }""")
        await page_gc.wait_for_timeout(3000)

        await page_gc.evaluate("""() => {
            for (const b of document.querySelectorAll('button')) {
                if (b.offsetParent && b.textContent.trim() === 'Novo') { b.click(); return; }
            }
        }""")
        await page_gc.wait_for_selector('.modal-interna-fundo .modal-titulo', timeout=10000)
        await page_gc.wait_for_timeout(1000)
        print("    OK modal Cadastro aberto")

        await page_gc.evaluate("""() => {
            for (const b of document.querySelectorAll('button')) {
                if (b.offsetParent && b.textContent.trim().includes('Importar Leiaute')) {
                    b.click(); return;
                }
            }
        }""")
        await page_gc.wait_for_timeout(2000)

        inp = await page_gc.query_selector('#arquivo')
        await inp.set_input_files(str(caminho))
        await page_gc.wait_for_timeout(2000)

        await page_gc.evaluate("""() => {
            const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
            const modal = modais[modais.length - 1];
            for (const b of modal.querySelectorAll('button')) {
                if (b.offsetParent && b.textContent.trim() === 'Enviar') { b.click(); return; }
            }
        }""")
        print("    OK .rem enviado, aguardando 6s...")
        await page_gc.wait_for_timeout(6000)

        await page_gc.evaluate("""() => {
            const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
            const modal = modais[modais.length - 1];
            const titulo = modal.querySelector('.modal-titulo')?.textContent?.trim() || '';
            if (titulo.includes('Leiaute')) {
                const x = modal.querySelector('.bx-fechar, .fa-xmark, [class*=\\"fechar\\"]');
                if (x) x.click();
            }
        }""")
        await page_gc.wait_for_timeout(1500)

        await page_gc.evaluate("""() => {
            for (const li of document.querySelectorAll('.aba-cabecalho-lista-li')) {
                if (li.textContent.trim() === 'Operacao' && li.offsetParent) { li.click(); return; }
                if (li.textContent.trim() === 'Operação' && li.offsetParent) { li.click(); return; }
            }
        }""")
        await page_gc.wait_for_timeout(2000)

        docs = await page_gc.evaluate("""() => {
            const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
            const out = [];
            for (const m of modais) {
                for (const tr of m.querySelectorAll('tbody tr')) {
                    if (!tr.offsetParent) continue;
                    const tds = [...tr.querySelectorAll('td')].map(td => (td.textContent||'').trim());
                    for (const t of tds) {
                        if (/^\\d{5,6}$/.test(t)) { out.push(t); break; }
                    }
                }
            }
            return out;
        }""")
        print(f"\n[6] Titulos importados: {docs}")

        for doc in docs:
            ok = await page_gc.evaluate(f"""() => {{
                for (const m of document.querySelectorAll('.modal-interna-fundo')) {{
                    if (!m.offsetParent) continue;
                    for (const tr of m.querySelectorAll('tbody tr')) {{
                        if (!tr.offsetParent) continue;
                        const tds = [...tr.querySelectorAll('td')].map(td => (td.textContent||'').trim());
                        if (!tds.includes('{doc}')) continue;
                        const btn = tr.querySelector('button[title=\\"Alterar\\"]');
                        if (btn) {{ btn.click(); return true; }}
                    }}
                }}
                return false;
            }}""")
            if not ok: continue
            await page_gc.wait_for_timeout(1200)
            campo = await page_gc.query_selector('#nume_nota')
            if campo:
                await campo.fill("")
                await campo.fill(doc)
                await page_gc.evaluate("""() => {
                    for (const b of document.querySelectorAll('button')) {
                        if (b.offsetParent && b.textContent.trim() === 'Salvar') { b.click(); return; }
                    }
                }""")
                await page_gc.wait_for_timeout(1500)
                print(f"    OK Num.Nota preenchido: {doc}")

        print("\n[7] PRONTO! Browser fica aberto 30s pra voce ver.")
        await page_gc.wait_for_timeout(30000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
