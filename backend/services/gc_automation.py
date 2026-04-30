"""
Automação GC Recursos — fluxo completo:
  1. GW  → gera arquivo .rem (Processos > Financeiro > Gerar arquivo de remessa)
  2. GC  → importa .rem (Operação > Digitação > Novo > Importar Leiaute)
  3. GC  → preenche Núm.Nota em cada título
  A finalização (definir conta corrente + encaminhar) é feita manualmente pelo usuário,
  igual ao que ocorre na Firma e FluxAsset.
"""

import re
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page
from browser_config import launch_kwargs

from config_manager import get_credencial

# ─── URLs ──────────────────────────────────────────────────────────────────────

BASE_GW = "https://webtrans.saas.gwsistemas.com.br"
GC_URL  = "http://gcrecursos.dyndns.org:9000/FactaConsult"

# ─── Conta bancária por sistema ────────────────────────────────────────────────

CONTA_POR_SISTEMA = {
    "gc_matriz": "3196-8",
    "gc_sp":     "03196-8",
}

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _hoje() -> str:
    from _tz import now_br
    return now_br().strftime("%d/%m/%Y")

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "automacao_factory"
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# ETAPA 1 — GW: gerar arquivo .rem
# ══════════════════════════════════════════════════════════════════════════════

async def gerar_remessa_gw(numeros_fatura: list[str], sistema: str, status: dict) -> Path | None:
    """
    Acessa GW > Processos > Financeiro > Gerar Arquivo de Remessa
    URL real: /jspexporta_boleto.jsp
    Filtra por data de emissão = hoje e conta bancária, marca apenas as
    faturas recebidas e baixa o arquivo .rem.
    """
    log = lambda msg: status["logs"].append(msg)
    creds_gw = get_credencial("gw", user_id=status.get("usuario_id"))
    conta    = CONTA_POR_SISTEMA.get(sistema, "")
    hoje     = _hoje()

    log(f"  GW — gerando remessa para conta '{conta}'...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs(headless=True))
        context = await browser.new_context(accept_downloads=True)
        page    = await context.new_page()

        # ── Login GW ──────────────────────────────────────────────────────────
        await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded", timeout=30000)
        await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
        await page.locator('input[name="login"]').fill(creds_gw["usuario"])
        await page.locator('input[name="senha"]').fill(creds_gw["senha"])
        await page.locator('button.button-login').click()
        try:
            await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        log(f"  GW — login OK")

        # ── Navega para Gerar Arquivo de Remessa ──────────────────────────────
        # URL real descoberta via inspeção do menu (li[href="./jspexporta_boleto.jsp"])
        await page.goto(f"{BASE_GW}/jspexporta_boleto.jsp", wait_until="load", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

        # ── Preenche filtros ──────────────────────────────────────────────────
        # Tipo de busca: Data de Emissão
        try:
            await page.select_option('select[name="campoDeConsulta"]', label="Data de Emissão")
        except Exception:
            pass

        # Datas já vêm pré-preenchidas com hoje — apenas confirma
        try:
            await page.fill('input[name="dtemissao1"]', hoje)
            await page.fill('input[name="dtemissao2"]', hoje)
        except Exception:
            pass

        # Conta bancária — MATCH EXATO pelo início do texto ("3196-8 / ...").
        # Crítico: `.includes('3196-8')` dava match também em "03196-8" (SP).
        # Usamos regex âncora ^ + espaço/barra como separador.
        if conta:
            try:
                opt_info = await page.evaluate(f"""() => {{
                    const s = document.querySelector('select[name="conta"]');
                    if (!s) return null;
                    const alvo = '{conta}';
                    // Match exato: texto começa com "<alvo> /" ou "<alvo> -" ou "<alvo>"
                    const re = new RegExp('^' + alvo.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&') + '(\\\\s|/|-|$)');
                    const opt = [...s.options].find(o => re.test(o.text.trim()));
                    return opt ? {{value: opt.value, text: opt.text}} : null;
                }}""")
                if opt_info:
                    await page.select_option('select[name="conta"]', value=opt_info["value"])
                    log(f"  Conta selecionada: {opt_info['text']} (value={opt_info['value']})")
                else:
                    log(f"  Conta '{conta}' nao encontrada no select (match exato)")
            except Exception as e:
                log(f"  Erro ao selecionar conta: {e}")

        # Apenas: "gerados / não gerados" (mostra ambos)
        try:
            await page.select_option('select[name="tipoGerado"]', label="gerados / não gerados")
        except Exception:
            try:
                # Fallback: tenta sem acento caso o DOM varie
                await page.select_option('select[name="tipoGerado"]', label="gerados / nao gerados")
            except Exception:
                pass

        # ── Pesquisar ─────────────────────────────────────────────────────────
        await page.click('input[name="pesquisar"]')
        try:
            await page.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        # ── Marca apenas as faturas selecionadas ──────────────────────────────
        # Tabela: col 0 = checkbox, col 1 = Fatura, col 2 = Nosso Número, ...
        marcadas = 0
        linhas = await page.query_selector_all("table tr")
        for linha in linhas:
            celulas = await linha.query_selector_all("td")
            if len(celulas) < 2:
                continue
            try:
                texto = (await celulas[1].inner_text()).strip()
                # Extrai "005148" de "005148/2026" ou "005148"
                m = re.match(r'^(\d{5,6})(?:/\d{4})?$', texto)
                if m:
                    num = m.group(1).zfill(6)
                    if num in numeros_fatura:
                        cb = await linha.query_selector('input[type="checkbox"]')
                        if cb:
                            await cb.check()
                            marcadas += 1
            except Exception:
                continue

        log(f"  {marcadas} fatura(s) marcada(s) para remessa")

        if marcadas == 0:
            log("  Nenhuma fatura encontrada na tela de remessa — abortando")
            await browser.close()
            return None

        # ── Exportar .rem ─────────────────────────────────────────────────────
        nome_arquivo = f"remessa_{sistema}_{hoje.replace('/', '')}.rem"
        caminho      = DOWNLOAD_DIR / nome_arquivo

        try:
            async with page.expect_download(timeout=30000) as dl_info:
                await page.click('input[value="Exportar Boletos"]')
            download = await dl_info.value
            await download.save_as(str(caminho))
            log(f"  Arquivo .rem salvo: {nome_arquivo}")
        except Exception as e:
            log(f"  Erro ao baixar .rem: {e}")
            await browser.close()
            return None

        await browser.close()
        return caminho


# ══════════════════════════════════════════════════════════════════════════════
# ETAPA 2 — GC: login
# ══════════════════════════════════════════════════════════════════════════════

async def fazer_login_gc(page: Page, sistema: str):
    """Login na plataforma GC Recursos."""
    creds = get_credencial(sistema)
    # GC fica em endpoint HTTP (port 9000) que pode ser lento/instável.
    # Usa timeout maior + retry pra cobrir variações de rede.
    last_exc = None
    for tentativa in range(1, 4):
        try:
            await page.goto(f"{GC_URL}/login", wait_until="domcontentloaded", timeout=90000)
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if tentativa < 3:
                await page.wait_for_timeout(3000)
    if last_exc:
        raise Exception(f"GC login não respondeu após 3 tentativas: {last_exc}")
    await page.locator('#Email').wait_for(state="visible", timeout=15000)
    await page.locator('#Email').fill(creds["usuario"])
    await page.locator('#Password').fill(creds["senha"])
    await page.locator('#btnEntrar').click()
    try:
        await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=20000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    await page.wait_for_timeout(1000)


# ══════════════════════════════════════════════════════════════════════════════
# ETAPA 3 — GC: importar arquivo .rem
# ══════════════════════════════════════════════════════════════════════════════

async def importar_remessa_gc(page: Page, caminho_rem: Path, status: dict) -> bool:
    """
    Navega para Operação > Digitação (SPA com roteamento por hash), clica Novo
    (abre modal 'Cadastro de títulos'), clica 'Importar Leiaute' (abre 2º modal
    'Carregar Arquivo - Leiaute'), sobe o .rem, clica Enviar e fecha o modal
    Leiaute. Ao fim, o modal principal fica aberto na aba Operação com os
    títulos já importados.
    """
    log = lambda msg: status["logs"].append(msg)

    # ── 1. Navega para Digitação via click no link (SPA com hash) ─────────────
    clicou = await page.evaluate("""() => {
        const a = document.querySelector('a[href*="/operacao/digitacao"]');
        if (!a) return false;
        a.click();
        return true;
    }""")
    if not clicou:
        log("  ⚠️ Link 'Digitação' não encontrado no menu")
        return False
    await page.wait_for_timeout(3000)

    # ── 2. Clica Novo → abre modal 'Cadastro de títulos' ──────────────────────
    clicou_novo = await page.evaluate("""() => {
        for (const b of document.querySelectorAll('button')) {
            if (b.offsetParent && b.textContent.trim() === 'Novo') { b.click(); return true; }
        }
        return false;
    }""")
    if not clicou_novo:
        log("  ⚠️ Botão 'Novo' não encontrado")
        return False
    try:
        await page.wait_for_selector('.modal-interna-fundo .modal-titulo', timeout=10000)
    except Exception:
        log("  ⚠️ Modal 'Cadastro de títulos' não abriu")
        return False
    await page.wait_for_timeout(1000)

    # ── 3. Clica 'Importar Leiaute' → abre 2º modal de upload ─────────────────
    clicou_imp = await page.evaluate("""() => {
        for (const b of document.querySelectorAll('button')) {
            if (b.offsetParent && b.textContent.trim().includes('Importar Leiaute')) {
                b.click(); return true;
            }
        }
        return false;
    }""")
    if not clicou_imp:
        log("  ⚠️ Botão 'Importar Leiaute' não encontrado")
        return False
    await page.wait_for_timeout(2000)

    # ── 4. Upload do .rem no input#arquivo (oculto, mas set_input_files funciona) ─
    try:
        input_file = await page.query_selector('#arquivo')
        if not input_file:
            log("  ⚠️ Campo #arquivo não encontrado no modal Leiaute")
            return False
        await input_file.set_input_files(str(caminho_rem))
        await page.wait_for_timeout(2000)
    except Exception as e:
        log(f"  ⚠️ Erro ao anexar .rem: {e}")
        return False

    # ── 5. Clica Enviar DENTRO do modal Leiaute (último modal aberto) ─────────
    clicou_env = await page.evaluate("""() => {
        const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
        const modal = modais[modais.length - 1];
        if (!modal) return false;
        for (const b of modal.querySelectorAll('button')) {
            if (b.offsetParent && b.textContent.trim() === 'Enviar') { b.click(); return true; }
        }
        return false;
    }""")
    if not clicou_env:
        log("  ⚠️ Botão 'Enviar' do modal Leiaute não encontrado")
        return False
    # Aguarda o servidor processar o .rem e importar os títulos
    await page.wait_for_timeout(6000)

    # ── 6. Fecha modal Leiaute (X) — o modal principal fica aberto ────────────
    await page.evaluate("""() => {
        const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
        const modal = modais[modais.length - 1];
        if (!modal) return;
        const titulo = modal.querySelector('.modal-titulo')?.textContent?.trim() || '';
        if (titulo.includes('Leiaute')) {
            const x = modal.querySelector('.bx-fechar, .fa-xmark, [class*="fechar"]');
            if (x) x.click();
        }
    }""")
    await page.wait_for_timeout(1500)

    log("  ✅ Arquivo .rem importado com sucesso")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# ETAPA 4 — GC: preencher Núm.Nota em cada título
# ══════════════════════════════════════════════════════════════════════════════

async def preencher_num_nota_gc(page: Page, status: dict) -> int:
    """
    O modal 'Cadastro de títulos' está aberto após importar_remessa_gc.
    Vai para aba 'Operação' (lista os títulos importados), e para cada linha:
      - lê Documento na coluna (nº de 5–6 dígitos)
      - clica botão 'Alterar' (botão com title="Alterar")
      - preenche #nume_nota com o mesmo número
      - clica Salvar
    """
    log = lambda msg: status["logs"].append(msg)

    # ── Vai para aba 'Operação' (lista os títulos importados) ─────────────────
    await page.evaluate("""() => {
        for (const li of document.querySelectorAll('.aba-cabecalho-lista-li')) {
            if (li.textContent.trim() === 'Operação' && li.offsetParent) { li.click(); return; }
        }
    }""")
    await page.wait_for_timeout(2000)

    total_preenchidos = 0

    # Coleta docs ANTES de iterar (cada Salvar recarrega a tabela)
    docs_linhas = await page.evaluate("""() => {
        const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
        const out = [];
        for (const m of modais) {
            for (const tr of m.querySelectorAll('tbody tr')) {
                if (!tr.offsetParent) continue;
                const tds = [...tr.querySelectorAll('td')].map(td => (td.textContent||'').trim());
                // Procura célula que é exatamente um número de 5–6 dígitos
                for (const t of tds) {
                    if (/^\\d{5,6}$/.test(t)) { out.push(t); break; }
                }
            }
        }
        return out;
    }""")
    log(f"  📋 Títulos encontrados na aba Operação: {docs_linhas}")

    for doc in docs_linhas:
        try:
            # Procura a linha que contém esse doc e clica o botão 'Alterar'
            clicou = await page.evaluate(f"""() => {{
                const alvo = '{doc}';
                const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
                for (const m of modais) {{
                    for (const tr of m.querySelectorAll('tbody tr')) {{
                        if (!tr.offsetParent) continue;
                        const tds = [...tr.querySelectorAll('td')].map(td => (td.textContent||'').trim());
                        if (!tds.includes(alvo)) continue;
                        const btn = tr.querySelector('button[title="Alterar"]');
                        if (btn) {{ btn.click(); return true; }}
                    }}
                }}
                return false;
            }}""")
            if not clicou:
                log(f"  ⚠️ Botão Alterar não achado para doc {doc}")
                continue
            # Aguarda aba 'Digitação' virar ativa com os campos preenchidos
            await page.wait_for_timeout(1200)

            # Preenche #nume_nota com o próprio número do documento
            campo = await page.query_selector('#nume_nota')
            if not campo:
                log(f"  ⚠️ Campo #nume_nota não encontrado para doc {doc}")
                continue
            try:
                await campo.fill("")
            except Exception:
                pass
            await campo.fill(doc)

            # Clica Salvar (botão visível do formulário de edição)
            salvou = await page.evaluate("""() => {
                for (const b of document.querySelectorAll('button')) {
                    if (b.offsetParent && b.textContent.trim() === 'Salvar') { b.click(); return true; }
                }
                return false;
            }""")
            if not salvou:
                log(f"  ⚠️ Botão Salvar não encontrado para doc {doc}")
                continue

            # Aguarda o Salvar processar e a aba voltar para Operação
            await page.wait_for_timeout(1200)
            total_preenchidos += 1
            log(f"  ✅ Núm.Nota preenchido: {doc}")

        except Exception as e:
            log(f"  ⚠️ Erro ao preencher doc {doc}: {e}")
            continue

    log(f"  ✅ Total de Núm.Nota preenchidos: {total_preenchidos}")
    return total_preenchidos


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def executar_gc(faturas_selecao, sistema: str, status: dict) -> dict:
    """
    Executa o fluxo completo da GC:
      1. Gera .rem no GW
      2. Importa .rem na GC
      3. Preenche Núm.Nota em cada título
    A finalização (definir conta corrente + encaminhar) fica para o usuário,
    igual ao comportamento das factories Firma e FluxAsset.
    """
    log = lambda msg: status["logs"].append(msg)

    faturas_dados = status.get("faturas_cache", {})
    numeros       = [sel.numero for sel in faturas_selecao]
    numeros_norm  = [n.zfill(6) for n in numeros]

    total_valor = sum(faturas_dados.get(n, {}).get("valor", 0) for n in numeros)
    total_qtd   = len(numeros)

    log(f"📋 GC {sistema}: {total_qtd} fatura(s) | Total: R$ {total_valor:,.2f}")

    # ── Etapa 1: gerar .rem no GW ────────────────────────────────────────────
    caminho_rem = await gerar_remessa_gw(numeros_norm, sistema, status)
    if not caminho_rem:
        status["erros"].append(f"GC {sistema}: falha ao gerar arquivo de remessa no GW")
        return {}

    # ── Etapas 2–3: importar e preencher na GC ───────────────────────────────
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs(headless=True))
        page    = await browser.new_page()

        log(f"🔑 GC {sistema} — fazendo login...")
        await fazer_login_gc(page, sistema)

        log("📂 Importando arquivo de remessa na GC...")
        sucesso = await importar_remessa_gc(page, caminho_rem, status)

        if sucesso:
            log("✏️ Preenchendo Núm.Nota em cada título...")
            preenchidos = await preencher_num_nota_gc(page, status)

            # IMPORTANTE: contabiliza apenas o que foi REALMENTE preenchido,
            # não o total esperado. Antes era += total_qtd (bug — marcava todas
            # como concluídas mesmo se só uma tivesse passado).
            if preenchidos > 0:
                status["concluidas"] += preenchidos
                # Só marca como "salva" as que realmente foram preenchidas
                # (ordem de preenchimento = ordem dos números na lista)
                for n in numeros[:preenchidos]:
                    status.setdefault("faturas_salvas", set()).add(n)

                if preenchidos == total_qtd:
                    log(f"✅ GC {sistema} — operação completa ({preenchidos}/{total_qtd}). Acesse a plataforma para definir conta corrente e encaminhar.")
                else:
                    faltam = total_qtd - preenchidos
                    log(f"⚠️ GC {sistema} — operação PARCIAL ({preenchidos}/{total_qtd}). {faltam} título(s) não preenchido(s).")
                    status["erros"].append(
                        f"GC {sistema}: apenas {preenchidos}/{total_qtd} título(s) preenchido(s) — verifique manualmente"
                    )
            else:
                status["erros"].append(f"GC {sistema}: nenhum Núm.Nota foi preenchido")
        else:
            status["erros"].append(f"GC {sistema}: falha na importação do arquivo .rem")

        await browser.close()

    return {"sistema": sistema, "qtd": total_qtd, "valor": total_valor}
