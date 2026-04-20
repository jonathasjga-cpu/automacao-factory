import asyncio
import zipfile
import io
import re
import traceback
from datetime import datetime
from urllib.parse import quote
from playwright.async_api import async_playwright, Page, BrowserContext
from config_manager import get_credencial

BASE_GW = "https://webtrans.saas.gwsistemas.com.br"

# ─── UTILS ───────────────────────────────────────────────────────────────────

def _hoje_fmt() -> str:
    return datetime.now().strftime("%d-%m-%Y")

def _hoje_gw() -> str:
    """DD/MM/AAAA para campos de data do GW"""
    return datetime.now().strftime("%d/%m/%Y")

def _ano_atual() -> str:
    return str(datetime.now().year)

def _nome_factory(sistema: str) -> str:
    return {
        "firma_matriz":     "Firma Matriz",
        "firma_sp":         "Firma SP",
        "fluxasset_matriz": "FluxAsset Matriz",
        "fluxasset_sp":     "FluxAsset SP",
        "gc_matriz":        "GC Matriz",
        "gc_sp":            "GC SP",
    }.get(sistema, sistema)

def _normalizar(num: str) -> str:
    """Remove zeros à esquerda: '005028' → '5028'"""
    return str(num).lstrip("0") or "0"

_FILIAL_ID = {
    "firma_matriz": "1", "firma_sp": "2",
    "fluxasset_matriz": "1", "fluxasset_sp": "2",
    "gc_matriz": "1", "gc_sp": "2",
}

# Labels da filial conforme aparecem no CTeControlador
_FILIAL_CTE = {
    "firma_matriz": "MATRIZ",
    "firma_sp":     "Filial SP",
    "fluxasset_matriz": "MATRIZ",
    "fluxasset_sp":     "Filial SP",
    "gc_matriz":    "MATRIZ",
    "gc_sp":        "Filial SP",
}

# ─── LOGIN ────────────────────────────────────────────────────────────────────

async def _login_gw(page: Page):
    creds = get_credencial("gw")
    await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(1500)
    await page.locator('input[name="login"]').fill(creds["usuario"])
    await page.locator('input[name="senha"]').fill(creds["senha"])
    await page.locator('button.button-login').click()
    await page.wait_for_url(f"{BASE_GW}/home", timeout=15000)
    await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(1500)

# ─── S3 CAPTURE ──────────────────────────────────────────────────────────────

async def _aguardar_s3_e_baixar(context: BrowserContext, trigger_fn) -> bytes | None:
    """
    Executa trigger_fn (click/navigate) e aguarda a URL S3 ser requisitada.
    O GW gera relatórios de forma assíncrona via redireciona_relatorio.jsp → S3.
    """
    s3_holder: dict = {}

    def capturar_s3(request):
        u = request.url
        if "gw-saas-relatorios.s3" in u and "gerados" in u:
            s3_holder["url"] = u

    context.on("request", capturar_s3)
    try:
        # Tenta capturar nova aba (popup) aberta pelo trigger
        try:
            async with context.expect_page(timeout=8000) as page_info:
                await trigger_fn()
            nova_aba = await page_info.value
            nova_aba.on("request", capturar_s3)
        except Exception:
            # Sem nova aba — trigger pode ter navegado na mesma página
            try:
                await trigger_fn()
            except Exception:
                pass  # URL pode já estar capturada

        # Aguarda até 60s pela URL S3
        for _ in range(40):
            await asyncio.sleep(1.5)
            if "url" in s3_holder:
                break

        if "url" not in s3_holder:
            return None

        resp = await context.request.get(s3_holder["url"])
        body = await resp.body()
        return body if body and len(body) > 500 else None
    finally:
        context.remove_listener("request", capturar_s3)


# ─── FATURAS PDF ─────────────────────────────────────────────────────────────
# Fluxo observado:
#   /consultafatura?acao=iniciar
#   → filtro Data de Emissão = hoje, filial
#   → selecionar checkboxes das faturas
#   → "Modelo de impressão em PDF" = Modelo 10
#   → clicar ícone PDF vermelho
#   → S3: faturamod10_<uuid>.pdf

async def baixar_faturas_pdf(
    faturas_por_factory: dict[str, list[dict]],
    status: dict,
):
    log = lambda msg: status["logs"].append(msg)
    resumo_docs = status.setdefault("resumo_documentos", {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        try:
            await _login_gw(page)

            for sistema, faturas in faturas_por_factory.items():
                if not faturas:
                    continue

                nome_factory = _nome_factory(sistema)
                numeros_raw = {f["numero"] for f in faturas}
                numeros_norm = {_normalizar(n) for n in numeros_raw}
                filial_id = _FILIAL_ID.get(sistema, "1")
                rd = resumo_docs.setdefault(sistema, {"nome": nome_factory, "boleto": None, "ctes": []})

                log(f"📄 Boleto — {nome_factory} ({len(numeros_raw)} fatura(s))...")
                log(f"  🔎 Faturas: {sorted(numeros_raw)}")

                try:
                    await page.goto(
                        f"{BASE_GW}/consultafatura?acao=iniciar",
                        wait_until="networkidle",
                        timeout=20000,
                    )
                    await page.wait_for_timeout(1200)

                    # Filtro: Data de Emissão = hoje
                    hoje = _hoje_gw()
                    await page.select_option('select[name="campoDeConsulta"]', value="emissao_fatura")
                    await page.fill('input[name="dtemissao1"]', hoje)
                    await page.fill('input[name="dtemissao2"]', hoje)
                    await page.select_option('select[name="filialId"]', value=filial_id)
                    await page.wait_for_timeout(300)

                    await page.click('input[value="Pesquisar"]')
                    await page.wait_for_load_state("networkidle", timeout=20000)
                    await page.wait_for_timeout(1500)

                    # Diagnóstico: faturas na página
                    na_pagina = []
                    for tr in await page.query_selector_all("tr"):
                        try:
                            texto = await tr.inner_text()
                            m = re.search(r"(\d{5,6})/\d{4}", texto)
                            if m:
                                na_pagina.append(m.group(1))
                        except Exception:
                            pass
                    log(f"  📋 GW retornou {len(na_pagina)} fatura(s): {na_pagina[:8]}")

                    # Desmarcar todos
                    for cb in await page.query_selector_all('input[type="checkbox"]'):
                        try:
                            if await cb.is_checked():
                                await cb.uncheck()
                        except Exception:
                            pass
                    await page.wait_for_timeout(300)

                    # Marcar faturas desta factory
                    marcadas = 0
                    for tr in await page.query_selector_all("tr"):
                        try:
                            texto = await tr.inner_text()
                            m = re.search(r"(\d{5,6})/\d{4}", texto)
                            if m and (_normalizar(m.group(1)) in numeros_norm or m.group(1) in numeros_raw):
                                cb = await tr.query_selector('input[type="checkbox"]')
                                if cb:
                                    await cb.check()
                                    marcadas += 1
                        except Exception:
                            continue

                    if marcadas == 0:
                        log(f"  ⚠️ Nenhuma fatura marcada. Esperado: {sorted(numeros_norm)} | Página: {na_pagina}")
                        rd["boleto"] = {"ok": False, "motivo": "fatura não encontrada no GW (data de hoje)"}
                        continue

                    log(f"  ✔ {marcadas} fatura(s) marcada(s)")

                    # Inspecionar página para encontrar select de Modelo e ícone PDF
                    page_info = await page.evaluate("""() => {
                        const selects = [...document.querySelectorAll('select')].map(s => ({
                            name: s.name, id: s.id,
                            opts: [...s.options].map(o => ({v: o.value, t: o.text.trim()}))
                        }));
                        const imgs = [...document.querySelectorAll('img')].map(img => ({
                            src: img.getAttribute('src') || '',
                            onclick: img.getAttribute('onclick') || '',
                            id: img.id || ''
                        })).filter(i => i.onclick || i.src.includes('pdf') || i.src.includes('relat'));
                        const links = [...document.querySelectorAll('a')].map(a => ({
                            text: a.innerText.trim(),
                            onclick: a.getAttribute('onclick') || '',
                            href: a.getAttribute('href') || ''
                        })).filter(a => a.onclick.includes('relat') || a.onclick.includes('gerar') || a.onclick.includes('PDF'));
                        return {selects, imgs, links};
                    }""")

                    log(f"  🔍 Selects: {[s['name'] for s in page_info['selects']]}")
                    log(f"  🔍 Imgs PDF: {page_info['imgs'][:4]}")
                    log(f"  🔍 Links: {page_info['links'][:3]}")

                    # Selecionar Modelo 10 no select de modelo
                    modelo_select = next(
                        (s for s in page_info['selects']
                         if any('odelo' in o['t'] for o in s['opts'])),
                        None
                    )
                    if modelo_select:
                        opt10 = next(
                            (o for o in modelo_select['opts'] if '10' in o['t']),
                            None
                        )
                        if opt10:
                            sel = f"select[name='{modelo_select['name']}']" if modelo_select['name'] else f"#{modelo_select['id']}"
                            await page.select_option(sel, value=opt10['v'])
                            log(f"  ✔ Modelo 10 selecionado ({opt10['t']})")
                        else:
                            log(f"  ⚠️ Modelo 10 não encontrado, opções: {[o['t'] for o in modelo_select['opts']]}")
                    else:
                        log(f"  ⚠️ Select de Modelo não encontrado")

                    await page.wait_for_timeout(300)

                    # Clicar no ícone PDF vermelho (gera relatório via S3)
                    # Identificado por onclick contendo termos de relatório/geração
                    pdf_bytes = await _aguardar_s3_e_baixar(
                        context,
                        lambda: page.evaluate("""() => {
                            // Procurar ícone PDF que gera relatório (NÃO o botão Imprimir Boletos)
                            // Padrões observados: img com onclick chamando funções de relatório
                            const candidates = [
                                ...document.querySelectorAll('img[onclick]'),
                                ...document.querySelectorAll('a[onclick]'),
                                ...document.querySelectorAll('input[type=image]'),
                            ];
                            for (const el of candidates) {
                                const oc = (el.getAttribute('onclick') || '').toLowerCase();
                                const src = (el.getAttribute('src') || '').toLowerCase();
                                // Excluir o botão de boleto explicitamente
                                if (oc.includes('boleto')) continue;
                                if (oc.includes('relatorio') || oc.includes('gerar') ||
                                    oc.includes('imprimir') || src.includes('pdf') ||
                                    src.includes('relat')) {
                                    el.click();
                                    return 'clicked: ' + el.getAttribute('onclick') + ' | src: ' + el.getAttribute('src');
                                }
                            }
                            // Último recurso: img com src de PDF
                            for (const img of document.querySelectorAll('img')) {
                                if ((img.src || '').toLowerCase().includes('pdf')) {
                                    img.click();
                                    return 'clicked by src: ' + img.src;
                                }
                            }
                            return 'NENHUM ELEMENTO ENCONTRADO';
                        }""")
                    )

                    if not pdf_bytes or b"%PDF" not in pdf_bytes[:10]:
                        log(f"  ⚠️ PDF de fatura não gerado — {nome_factory}")
                        rd["boleto"] = {"ok": False, "motivo": "PDF S3 não capturado (verifique logs de diagnóstico)"}
                        continue

                    nome_arquivo = f"Boleto - {nome_factory} - {_hoje_fmt()}.pdf"
                    status.setdefault("arquivos", {})[nome_arquivo] = pdf_bytes
                    log(f"  ✅ Salvo: {nome_arquivo} ({len(pdf_bytes):,} bytes)")
                    rd["boleto"] = {"ok": True, "arquivo": nome_arquivo, "qtd": marcadas}

                except Exception as e:
                    log(f"  ❌ Erro boleto {nome_factory}: {e}")
                    rd["boleto"] = {"ok": False, "motivo": str(e)[:120]}

        except Exception as e:
            log(f"  ❌ Erro geral boletos: {e}")
            log(traceback.format_exc()[-600:])
        finally:
            await browser.close()


# ─── CTes PDF ────────────────────────────────────────────────────────────────
# Fluxo observado:
#   /CTeControlador?acao=listar
#   → filtro "Número Fatura" + número + ano + filial (MATRIZ/Filial SP)
#   → Pesquisar → resultados com IDs dos CTes
#   → navegar para redireciona_relatorio.jsp?url=./listar_cte.jsp?acao=exportar&modelo=17&idCte=ID1,ID2,...
#   → S3: dacte_mod17_<uuid>.pdf (todos CTes da fatura agrupados num único PDF)

async def baixar_ctes_pdf(
    faturas_por_factory: dict[str, list[dict]],
    status: dict,
):
    log = lambda msg: status["logs"].append(msg)
    resumo_docs = status.setdefault("resumo_documentos", {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        try:
            await _login_gw(page)

            for sistema, faturas in faturas_por_factory.items():
                if not faturas:
                    continue

                nome_factory = _nome_factory(sistema)
                filial_cte = _FILIAL_CTE.get(sistema, "MATRIZ")
                pdfs_desta_factory: list[Path] = []
                rd = resumo_docs.setdefault(sistema, {"nome": nome_factory, "boleto": None, "ctes": []})
                ctes_info: list[dict] = []

                log(f"📋 CTes — {nome_factory} ({len(faturas)} fatura(s))...")

                for fatura in faturas:
                    numero = fatura["numero"]
                    # Número sem zeros à esquerda (ex: "005028" → "5028") e sem "/ano"
                    numero_busca = _normalizar(numero.split("/")[0].strip() if "/" in numero else numero)
                    ano_busca = _ano_atual()

                    log(f"  🔍 Fatura {numero} → busca: '{numero_busca}' / '{ano_busca}' / filial '{filial_cte}'")

                    try:
                        await page.goto(
                            f"{BASE_GW}/CTeControlador?acao=listar",
                            wait_until="networkidle",
                            timeout=20000,
                        )
                        await page.wait_for_timeout(1500)

                        # Inspecionar campos do formulário
                        form_info = await page.evaluate("""() => {
                            const selects = [...document.querySelectorAll('select')].map(s => ({
                                name: s.name, id: s.id, value: s.value,
                                opts: [...s.options].map(o => ({v: o.value, t: o.text.trim()}))
                            }));
                            const inputs = [...document.querySelectorAll('input[type="text"], input:not([type])')].filter(
                                i => !i.disabled && i.offsetParent !== null
                            ).map(i => ({name: i.name, id: i.id, value: i.value, placeholder: i.placeholder}));
                            return {selects, inputs};
                        }""")

                        log(f"    Selects: {[s['name'] for s in form_info['selects']]}")
                        log(f"    Inputs: {[(i['name'], i['value']) for i in form_info['inputs'][:6]]}")

                        # Preencher formulário via JavaScript (adaptável a seletores desconhecidos)
                        fill_result = await page.evaluate("""([numero, ano, filial]) => {
                            const passos = [];
                            const allSelects = [...document.querySelectorAll('select')];
                            const allInputs = [...document.querySelectorAll('input[type="text"], input:not([type])')].filter(
                                i => !i.disabled && i.offsetParent !== null
                            );

                            // 1. Setar campo de busca para "Número Fatura"
                            for (const s of allSelects) {
                                const numFatOpt = [...s.options].find(o =>
                                    /n[úu]mero.*(fatura|fat)/i.test(o.text) ||
                                    /(fatura|fat).*n[úu]mero/i.test(o.text)
                                );
                                if (numFatOpt) {
                                    s.value = numFatOpt.value;
                                    s.dispatchEvent(new Event('change', {bubbles: true}));
                                    passos.push('campo_busca=' + s.name + ':' + numFatOpt.value);
                                    break;
                                }
                            }

                            // 2. Preencher número e ano
                            // Encontrar inputs pelo name/id pattern ou por posição
                            let inpNumero = allInputs.find(i =>
                                /numero|valor|busca|numero_fat/i.test(i.name + i.id)
                            ) || allInputs[0];

                            let inpAno = allInputs.find(i =>
                                /ano|year|exercicio/i.test(i.name + i.id)
                            ) || allInputs.find(i => i.value === new Date().getFullYear().toString())
                              || allInputs[1];

                            if (inpNumero) {
                                inpNumero.value = numero;
                                inpNumero.dispatchEvent(new Event('input', {bubbles: true}));
                                inpNumero.dispatchEvent(new Event('change', {bubbles: true}));
                                passos.push('numero=' + inpNumero.name + ':' + numero);
                            }
                            if (inpAno && inpAno !== inpNumero) {
                                inpAno.value = ano;
                                inpAno.dispatchEvent(new Event('input', {bubbles: true}));
                                inpAno.dispatchEvent(new Event('change', {bubbles: true}));
                                passos.push('ano=' + inpAno.name + ':' + ano);
                            }

                            // 3. Setar filial
                            for (const s of allSelects) {
                                const opts = [...s.options];
                                if (!opts.some(o => /matriz|filial.sp/i.test(o.text))) continue;
                                const filialOpt = opts.find(o =>
                                    o.text.trim() === filial ||
                                    o.text.trim().toUpperCase() === filial.toUpperCase()
                                );
                                if (filialOpt) {
                                    s.value = filialOpt.value;
                                    s.dispatchEvent(new Event('change', {bubbles: true}));
                                    passos.push('filial=' + s.name + ':' + filialOpt.value);
                                    break;
                                }
                            }

                            return passos;
                        }""", [numero_busca, ano_busca, filial_cte])

                        log(f"    Preenchimento: {fill_result}")

                        # Pesquisar
                        await page.click('input[value="Pesquisar"]')
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        await page.wait_for_timeout(2000)

                        # Verificar ocorrências
                        page_text = await page.inner_text("body")
                        m_occ = re.search(r"Ocorr[êe]ncias:\s*(\d+)", page_text)
                        total_ctes = int(m_occ.group(1)) if m_occ else -1
                        log(f"    Ocorrências: {total_ctes}")

                        # Extrair IDs dos CTes dos checkboxes
                        cte_ids = await page.evaluate("""() => {
                            const ids = new Set();
                            // Checkboxes com valores numéricos (IDs do banco de dados)
                            for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
                                const v = cb.value || '';
                                if (/^\d{4,}$/.test(v)) ids.add(v);
                            }
                            // Fallback: inputs hidden com "id" ou "cte" no nome
                            if (ids.size === 0) {
                                for (const inp of document.querySelectorAll('input[type="hidden"]')) {
                                    const n = (inp.name || '').toLowerCase();
                                    if ((n.includes('id') || n.includes('cte')) && /^\d{4,}$/.test(inp.value)) {
                                        ids.add(inp.value);
                                    }
                                }
                            }
                            return [...ids];
                        }""")

                        log(f"    IDs capturados ({len(cte_ids)}): {cte_ids[:5]}")

                        if not cte_ids:
                            motivo = "nenhum CTe encontrado" if total_ctes == 0 else "IDs não capturados (verifique logs)"
                            log(f"  ⚠️ {motivo} — fatura {numero}")
                            ctes_info.append({"numero": numero, "ok": False, "qtd": 0, "motivo": motivo})
                            continue

                        # Navegar diretamente para a URL de exportação
                        # (mesmo padrão observado: redireciona_relatorio.jsp → S3)
                        ids_str = ",".join(cte_ids)
                        inner = f"./listar_cte.jsp?acao=exportar&modelo=17&idCte={ids_str}"
                        export_url = f"{BASE_GW}/redireciona_relatorio.jsp?url={quote(inner)}"
                        log(f"    Exportando {len(cte_ids)} CTe(s) via S3...")

                        pdf_bytes = await _aguardar_s3_e_baixar(
                            context,
                            lambda u=export_url: page.goto(u, wait_until="domcontentloaded", timeout=15000),
                        )

                        if not pdf_bytes or b"%PDF" not in pdf_bytes[:10]:
                            log(f"  ⚠️ PDF CTe não capturado — fatura {numero}")
                            ctes_info.append({"numero": numero, "ok": False, "qtd": 0, "motivo": "PDF S3 não capturado"})
                            continue

                        nome_arquivo = f"CTe - {nome_factory} - Fatura {numero}.pdf"
                        status.setdefault("arquivos", {})[nome_arquivo] = pdf_bytes
                        pdfs_desta_factory.append((nome_arquivo, pdf_bytes))

                        log(f"  ✅ {len(cte_ids)} CTe(s) — fatura {numero} ({len(pdf_bytes):,} bytes)")
                        ctes_info.append({"numero": numero, "ok": True, "qtd": len(cte_ids)})

                    except Exception as e:
                        log(f"  ❌ Fatura {numero}: {e}")
                        ctes_info.append({"numero": numero, "ok": False, "qtd": 0, "motivo": str(e)[:120]})
                        continue

                rd["ctes"] = ctes_info

                if pdfs_desta_factory:
                    nome_zip = f"CTEs - {nome_factory} - {_hoje_fmt()}.zip"
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        for nome_pdf, dados_pdf in pdfs_desta_factory:
                            zf.writestr(nome_pdf, dados_pdf)
                    status.setdefault("arquivos", {})[nome_zip] = buf.getvalue()
                    log(f"  📦 ZIP: {nome_zip} ({len(pdfs_desta_factory)} fatura(s))")
                    rd["zip"] = {"ok": True, "arquivo": nome_zip, "qtd": len(pdfs_desta_factory)}
                else:
                    rd["zip"] = {"ok": False}

        except Exception as e:
            log(f"  ❌ Erro geral CTes: {e}")
            log(traceback.format_exc()[-600:])
        finally:
            await browser.close()


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

async def executar_salvamento_documentos(
    faturas_por_factory: dict[str, list[dict]],
    status: dict,
):
    log = lambda msg: status["logs"].append(msg)

    status.setdefault("resumo_documentos", {})
    status.setdefault("arquivos", {})

    log("=" * 50)
    log("📥 Gerando documentos para download...")
    log("=" * 50)

    log("📄 ETAPA 1: Baixando boletos das faturas...")
    try:
        await baixar_faturas_pdf(faturas_por_factory, status)
    except Exception as e:
        log(f"❌ ETAPA 1 falhou com exceção: {e}")
        log(traceback.format_exc()[-800:])

    log("📋 ETAPA 2: Baixando CTes e criando ZIPs...")
    try:
        await baixar_ctes_pdf(faturas_por_factory, status)
    except Exception as e:
        log(f"❌ ETAPA 2 falhou com exceção: {e}")
        log(traceback.format_exc()[-800:])

    rd = status.get("resumo_documentos", {})
    total_boletos_ok = sum(1 for v in rd.values() if v.get("boleto") and v["boleto"].get("ok"))
    total_ctes_ok = sum(
        sum(1 for c in v.get("ctes", []) if c.get("ok")) for v in rd.values()
    )
    total_zips = sum(1 for v in rd.values() if v.get("zip") and v["zip"].get("ok"))
    total_arquivos = len(status.get("arquivos", {}))

    log("=" * 50)
    if total_arquivos:
        log(f"✅ {total_arquivos} arquivo(s) prontos para download")
        log(f"   📄 {total_boletos_ok} boleto(s) PDF")
        log(f"   📋 {total_ctes_ok} CTe(s) PDF agrupados por fatura")
        log(f"   📦 {total_zips} ZIP(s)")
    else:
        log("⚠️ Nenhum documento gerado — verifique os logs acima")
    log("=" * 50)
