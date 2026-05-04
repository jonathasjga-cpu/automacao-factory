import asyncio
import json
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import os

from playwright.async_api import async_playwright
from browser_config import launch_kwargs
from config_manager import get_credencial

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "automacao_factory"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Arquivo de persistÃªncia da cache (sobrevive a reinicializaÃ§Ãµes do backend)
_CACHE_FILE = DOWNLOAD_DIR / "cache_faturas.json"

# Cache global para uso nos mÃ³dulos de automaÃ§Ã£o
_cache_faturas: list[dict] = []

def _salvar_cache(faturas: list[dict]):
    """Persiste a cache em disco para sobreviver a reinicializaÃ§Ãµes."""
    try:
        _CACHE_FILE.write_text(json.dumps(faturas, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _carregar_cache() -> list[dict]:
    """Carrega a cache do disco se existir."""
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

# Tenta carregar cache persistida ao importar o mÃ³dulo
_cache_faturas = _carregar_cache()

# ─── Progresso da fase "Carregar do GW" ─────────────────────────────────────
# Usado para o frontend mostrar logs ao vivo enquanto o botão fica "Baixando...".
_progresso: dict = {"status": "ocioso", "logs": [], "inicio": None, "fim": None}

def _prog_reset():
    _progresso["status"] = "executando"
    _progresso["logs"] = []
    from _tz import now_br
    _progresso["inicio"] = now_br().isoformat()
    _progresso["fim"] = None

def _prog_log(msg: str):
    _progresso["logs"].append(msg)

def _prog_finalizar(ok: bool, erro: str | None = None):
    _progresso["status"] = "concluido" if ok else "erro"
    from _tz import now_br
    _progresso["fim"] = now_br().isoformat()
    if erro:
        _progresso["logs"].append(f"❌ {erro}")

def get_progresso_carregar() -> dict:
    """Retorna o estado atual do processo de carregamento (expost via /api)."""
    return {
        "status": _progresso["status"],
        "logs": list(_progresso["logs"]),
        "inicio": _progresso["inicio"],
        "fim": _progresso["fim"],
    }

def serial_excel_para_data(serial) -> str:
    """Converte nÃºmero serial do Excel para data string DD/MM/AAAA"""
    if pd.isna(serial):
        return ""
    try:
        data = datetime(1899, 12, 30) + timedelta(days=int(serial))
        return data.strftime("%d/%m/%Y")
    except:
        return str(serial)

async def _aguardar_e_baixar(page, context, nome: str, meus_rel_url: str, tentativas: int = 8) -> Path:
    """Retorna o arquivo jÃ¡ capturado durante _gerar_relatorio_personalizado, ou busca em Meus RelatÃ³rios."""
    # Verifica se o download jÃ¡ foi capturado durante a geraÃ§Ã£o
    downloads = getattr(context, '_last_download', {})
    if nome in downloads and downloads[nome].exists() and _is_valid_excel(downloads[nome]):
        return downloads[nome]

    # Fallback: busca em Meus RelatÃ³rios com polling
    import unicodedata
    def norm(s):
        return unicodedata.normalize("NFC", s).lower()

    for tentativa in range(tentativas):
        # domcontentloaded é responsivo; wait_for_function abaixo confirma a tabela
        await page.goto(meus_rel_url, wait_until="domcontentloaded", timeout=30000)
        # Aguarda tabela renderizar — sai imediato quando há linhas
        try:
            await page.wait_for_function(
                "() => document.querySelectorAll('tr').length > 1",
                timeout=8000,
            )
        except Exception:
            pass
        rows = await page.query_selector_all("tr")
        # Não usar any(await ... for row in rows) — cria async generator que any() não itera
        encontrado = False
        for row in rows:
            try:
                txt = await row.inner_text()
                if norm(nome) in norm(txt):
                    encontrado = True
                    break
            except Exception:
                continue
        if encontrado:
            return await _baixar_meu_relatorio(page, context, nome, meus_rel_url)
        if tentativa < tentativas - 1:
            # Espera curta entre re-checks de Meus Relatórios — relatório pode levar
            # alguns segundos pra processar; 2s é bem mais responsivo que 5s.
            await page.wait_for_timeout(2000)

    raise Exception(
        f"RelatÃ³rio '{nome}' nÃ£o encontrado. Verifique se existe um relatÃ³rio personalizado "
        "com esse nome no GW (RelatÃ³rios > RelatÃ³rios Personalizados)."
    )


async def _gerar_relatorio_personalizado(page, nome_relatorio: str, data_hoje: str, base: str, preencher_data: bool = True, context=None, navegar: bool = True):
    """
    Seleciona o relatório personalizado pelo nome, preenche data de emissão e clica em Gerar.

    navegar=True  → primeira chamada: navega até a URL e clica na aba Relatórios Personalizados
    navegar=False → segunda chamada: página já está aberta na aba certa, só clica no radio
    """
    # Registra handler de dialog antes de qualquer ação (cobre notificações do GW)
    page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))


    # Sempre navega para garantir estado limpo. domcontentloaded é responsivo;
    # wait_for_function abaixo confirma que a UI renderizou.
    url_rel = f"{base}/relcontasreceber?acao=iniciar&modulo=webtrans"
    await page.goto(url_rel, wait_until="domcontentloaded", timeout=30000)
    # Aguarda a página de relatórios renderizar (alguma aba/td/tr clicável). Sai imediato.
    try:
        await page.wait_for_function(
            "() => document.querySelectorAll('td, th, tr').length > 5",
            timeout=8000,
        )
    except Exception:
        pass

    # Se GW retornou 403, a sessão expirou — relança erro claro
    titulo = await page.title()
    if "403" in titulo or "403" in page.url:
        raise Exception("GW retornou 403 (sessão expirada). Tente novamente.")

    # Clica na aba "Relatórios Personalizados"
    await page.evaluate("""
        () => {
            for (const el of document.querySelectorAll('td, th, div, button, a, span, input')) {
                const txt = (el.textContent || el.value || '').trim();
                if (txt === 'Relatórios Personalizados') { el.click(); return; }
            }
        }
    """)

    # Aguarda os radio buttons aparecerem (confirma que a lista carregou)
    try:
        await page.wait_for_selector('input[type="radio"]', timeout=8000)
    except Exception:
        pass

    # Seleciona o radio button pelo nome do relatório
    # Usa normalização NFC para garantir comparação correta de acentos
    nome_lower = nome_relatorio.lower()
    # Primeira palavra distintiva (ex: "complemento", "automação")
    nome_first = nome_lower.split()[0]

    selecionado = await page.evaluate(f"""
        () => {{
            function norm(s) {{
                return (s || '').normalize('NFC').toLowerCase().trim();
            }}
            const nome = norm({repr(nome_lower)});
            const nomeFirst = norm({repr(nome_first)});

            // Estratégia 1: td cujo texto contém o nome — verifica irmãos anterior e posterior
            for (const td of document.querySelectorAll('td')) {{
                const txt = norm(td.textContent);
                if (txt.includes(nome) || (nomeFirst.length >= 5 && txt.includes(nomeFirst))) {{
                    const radio = td.querySelector('input[type="radio"]')
                               || td.previousElementSibling?.querySelector('input[type="radio"]')
                               || td.nextElementSibling?.querySelector('input[type="radio"]');
                    if (radio) {{ radio.click(); return 'td:' + td.textContent.trim().substring(0,60); }}
                }}
            }}
            // Estratégia 2: radio cujo tr pai contém o nome
            for (const r of document.querySelectorAll('input[type="radio"]')) {{
                const row = r.closest('tr') || r.parentElement;
                if (row) {{
                    const rowTxt = norm(row.textContent);
                    if (rowTxt.includes(nome) || (nomeFirst.length >= 5 && rowTxt.includes(nomeFirst))) {{
                        r.click(); return 'tr:' + row.textContent.trim().substring(0,60);
                    }}
                }}
            }}
            // Diagnóstico: lista todos os textos de tds e radios disponíveis
            const tds = [...document.querySelectorAll('td')].map(t => t.textContent.trim()).filter(t => t.length > 2);
            const radios = document.querySelectorAll('input[type="radio"]').length;
            return 'NAO_ENCONTRADO. radios=' + radios + ' Tds: ' + tds.slice(0,15).join(' | ');
        }}
    """)

    # Log do resultado para diagnóstico
    # (selecionado contém o resultado do radio_select para diagnóstico inline)

    # Lança exceção se o radio não foi encontrado
    if isinstance(selecionado, str) and selecionado.startswith('NAO_ENCONTRADO'):
        raise Exception(f"Radio '{nome_relatorio}' não encontrado na lista do GW. {selecionado}")

    # Aguarda os filtros do relatório atualizarem — espera responsivamente que algum
    # input de data apareça (sinal de que a UI reagiu ao radio click). Sai imediato.
    try:
        await page.wait_for_function(
            """() => {
                const trs = [...document.querySelectorAll('tr')];
                return trs.some(tr => {
                    const t = (tr.textContent || '').toLowerCase();
                    return t.includes('emiss') && tr.querySelectorAll('input[type=\\"text\\"]').length >= 2;
                });
            }""",
            timeout=4000,
        )
    except Exception:
        pass

    if preencher_data:
        # Identifica os 2 inputs de data via JS (retorna ids ou seletores absolutos)
        seletores_data = await page.evaluate("""() => {
            const trs = [...document.querySelectorAll('tr')];
            for (const tr of trs) {
                const textoLinha = (tr.textContent || '').toLowerCase();
                if (textoLinha.includes('emiss')) {
                    const inputs = [...tr.querySelectorAll('input[type="text"]')].slice(0, 2);
                    if (inputs.length >= 2) {
                        // Marca cada input com data-fillme=1,2 para podermos selecionar via Playwright
                        inputs[0].setAttribute('data-fillme', 'd1');
                        inputs[1].setAttribute('data-fillme', 'd2');
                        return ['[data-fillme="d1"]', '[data-fillme="d2"]'];
                    }
                }
            }
            return [];
        }""")
        if seletores_data and len(seletores_data) >= 2:
            # Usa page.fill que dispara eventos nativos (mais confiável que evaluate em headless)
            for sel in seletores_data:
                try:
                    await page.locator(sel).first.fill(data_hoje)
                except Exception as e:
                    print(f"[gerar_rel] AVISO: fill {sel} falhou: {e}")
            # Confirma valor preenchido (debug)
            valores = await page.evaluate("""() => {
                const a = document.querySelector('[data-fillme=\\"d1\\"]');
                const b = document.querySelector('[data-fillme=\\"d2\\"]');
                return [a ? a.value : null, b ? b.value : null];
            }""")
            print(f"[gerar_rel] datas preenchidas: dt1={valores[0]!r} dt2={valores[1]!r} (esperado={data_hoje!r})")
        else:
            print(f"[gerar_rel] AVISO: nao achou inputs de data — relatorio pode vir sem filtro")

    # Garante formato Excel selecionado (primeiro radio antes do botão Gerar = XLS)
    await page.evaluate("""
        () => {
            const btn = [...document.querySelectorAll('input[type="submit"], input[type="button"]')]
                .find(el => el.value && el.value.includes('Gerar'));
            if (!btn) return;
            const linhaFormato = btn.closest('tr')?.previousElementSibling;
            if (linhaFormato) {
                const radios = linhaFormato.querySelectorAll('input[type="radio"]');
                if (radios[0]) radios[0].click();
            }
        }
    """)

    # Aceita dialogs JS automáticos
    page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

    async def _clicar_gerar():
        # Aguarda o botão Gerar estar disponível antes de clicar (sem sleep fixo)
        try:
            await page.wait_for_function(
                """() => {
                    const btns = [...document.querySelectorAll('input[type="submit"], input[type="button"]')];
                    return btns.some(b => b.value && b.value.includes('Gerar'));
                }""",
                timeout=3000,
            )
        except Exception:
            pass
        await page.evaluate("""
            () => {
                const btn = [...document.querySelectorAll('input[type="submit"], input[type="button"]')]
                    .find(el => el.value && el.value.includes('Gerar'));
                if (btn) btn.click();
            }
        """)

    # Clica em "Gerar Relatório" e aguarda nova aba com o download
    if context:
        try:
            async with context.expect_page(timeout=15000) as popup_info:
                await _clicar_gerar()
            popup = await popup_info.value
            # Aguarda o popup carregar (GW exibe "Seu relatório já foi gerado. Clique no link...")
            await popup.wait_for_load_state("domcontentloaded", timeout=30000)
            # Aguarda link aparecer no popup — sinal responsivo de que a tela renderizou
            try:
                await popup.wait_for_function(
                    "() => document.querySelectorAll('a').length > 0",
                    timeout=8000,
                )
            except Exception:
                pass
            try:
                async with popup.expect_download(timeout=60000) as dl_info:
                    # GW não faz download automático — precisa clicar no link azul do popup
                    await popup.evaluate("""
                        () => {
                            const links = [...document.querySelectorAll('a')];
                            if (links.length > 0) links[0].click();
                        }
                    """)
                download = await dl_info.value
                dest = DOWNLOAD_DIR / f"{nome_relatorio.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                await download.save_as(str(dest))
                if not hasattr(context, '_last_download'):
                    context._last_download = {}
                context._last_download[nome_relatorio] = dest
            except Exception:
                pass  # fallback para Meus Relatórios em _aguardar_e_baixar
            try:
                await popup.close()
            except Exception:
                pass
        except Exception:
            await _clicar_gerar()
    else:
        await _clicar_gerar()

    # Margem mínima para o GW iniciar o processamento do relatório.
    # O fallback _aguardar_e_baixar já faz polling — não precisa de sleep grande aqui.
    await page.wait_for_timeout(800)


async def baixar_relatorios_gw(user_id: int | None = None) -> tuple[Path, Path | None]:
    """
    Gera os relatórios personalizados no GW e baixa via popup.
    - Automação: filtra por emissão = hoje (ou ontem se hoje estiver vazio)
    - Complemento: sem filtro de data (retorna todo o histórico, ~2.6 MB)
    """
    from _tz import now_br
    creds = get_credencial("gw", user_id=user_id)
    base = "https://webtrans.saas.gwsistemas.com.br"
    hoje = now_br().strftime("%d/%m/%Y")
    ontem = (now_br() - timedelta(days=1)).strftime("%d/%m/%Y")
    meus_rel_url = f"{base}/RelatorioControlador?acao=abrirTelaMeusRelatorios"

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs(headless=False))
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        # Login
        _prog_log("🔑 Fazendo login no GW...")
        await page.goto(f"{base}/login", wait_until="domcontentloaded")
        await page.wait_for_selector('#login', timeout=15000)
        await page.fill('#login', creds["usuario"])
        await page.fill('#senha', creds["senha"])
        await page.click('.button-login')
        # Aguarda redirect pra fora do login (sai assim que muda)
        try:
            await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        except Exception:
            pass

        # Verifica se ainda está na tela de login (credenciais erradas ou problema no login)
        current_url = page.url
        if "login" in current_url.lower():
            raise Exception("Login GW falhou. Verifique as credenciais em Configurações.")

        # Aguarda a home carregar para garantir sessão ativa antes de navegar.
        # domcontentloaded + wait_for_function = saída no momento certo, sem
        # esperar todos os recursos da home.
        await page.goto(f"{base}/home", wait_until="domcontentloaded", timeout=30000)
        # Espera sinal de UI renderizada (link/iframe da home) — responsivo
        try:
            await page.wait_for_function(
                "() => !!document.querySelector('a[href], iframe, [onclick]')",
                timeout=8000,
            )
        except Exception:
            pass

        # Se caiu em 403 ou redirect de volta ao login, lança erro
        current_url = page.url
        if "login" in current_url.lower() or "403" in current_url.lower():
            raise Exception("Sessão GW inválida após login. Tente novamente.")

        # Gera Automação com data de hoje
        _prog_log("📄 Gerando relatório 'Automação Operações'...")
        await _gerar_relatorio_personalizado(
            page, "Automação Operações - Jonathas", hoje, base, context=context
        )
        _prog_log("⬇️ Baixando 'Automação Operações'...")
        arquivo1 = await _aguardar_e_baixar(
            page, context, "Automação Operações - Jonathas", meus_rel_url
        )
        _prog_log("✓ Arquivo 'Automação' baixado")

        # Complemento: página já está aberta na aba Relatórios Personalizados após o Automação
        # Usa navegar=False para não recarregar — só clica no radio e preenche a data
        arquivo2 = None
        try:
            _prog_log("📄 Gerando relatório 'Complemento Operações'...")
            await _gerar_relatorio_personalizado(
                page, "Complemento Operações - Jonathas", hoje, base,
                preencher_data=True, context=context, navegar=False
            )
            _prog_log("⬇️ Baixando 'Complemento Operações'...")
            arquivo2 = await _aguardar_e_baixar(
                page, context, "Complemento Operações - Jonathas", meus_rel_url
            )
            _prog_log("✓ Arquivo 'Complemento' baixado")
        except Exception as e:
            raise Exception(f"[Complemento] Falha: {e}")

        await browser.close()
        return arquivo1, arquivo2

def _is_valid_excel(path: Path) -> bool:
    """Verifica se o arquivo Ã© um Excel real (magic bytes PK = zip/xlsx)"""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        return magic == b"PK\x03\x04"
    except Exception:
        return False


async def _baixar_meu_relatorio(page, context, nome: str, url: str = None) -> Path:
    """Clica em 'Baixar Excel' para o relatÃ³rio mais recente com o nome dado"""
    prefixo = nome.replace(' ', '_')
    caminho = DOWNLOAD_DIR / f"{prefixo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    import unicodedata
    import httpx

    def norm(s):
        return unicodedata.normalize("NFC", s).lower()

    # Busca a linha do relatÃ³rio na tabela
    rows = await page.query_selector_all("tr")
    linha_alvo = None
    for row in rows:
        txt = await row.inner_text()
        if norm(nome) in norm(txt):
            linha_alvo = row
            break

    if not linha_alvo:
        raise Exception(
            f"RelatÃ³rio '{nome}' nÃ£o encontrado em Meus RelatÃ³rios. "
            "Gere-o manualmente no GW (RelatÃ³rios > Meus RelatÃ³rios) e tente novamente."
        )

    # Tenta clicar em "Gerar" / "Atualizar" para garantir link S3 fresco
    links_linha = await linha_alvo.query_selector_all("a, button")
    for el in links_linha:
        el_txt = (await el.inner_text()).lower()
        if "gerar" in el_txt or "atualizar" in el_txt or "processar" in el_txt:
            await el.click()
            # Espera responsiva: faz polling rápido recarregando a página até
            # aparecer link de "Excel/Baixar" na linha alvo (sinal de que está pronto).
            # Cap de 15s para não travar.
            url_rel = "https://webtrans.saas.gwsistemas.com.br/RelatorioControlador?acao=abrirTelaMeusRelatorios"
            import time as _time
            inicio_wait = _time.monotonic()
            while _time.monotonic() - inicio_wait < 15:
                await page.wait_for_timeout(800)
                try:
                    await page.goto(url_rel, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    continue
                rows = await page.query_selector_all("tr")
                pronto = False
                for row in rows:
                    txt = await row.inner_text()
                    if norm(nome) in norm(txt):
                        linha_alvo = row
                        # Há link clicável de download/excel?
                        for a in await row.query_selector_all("a"):
                            atxt = (await a.inner_text()).lower()
                            if "excel" in atxt or "baixar" in atxt:
                                pronto = True
                                break
                        break
                if pronto:
                    break
            break

    # Captura URL do S3 via route interception e baixa com httpx
    links = await linha_alvo.query_selector_all("a")
    for link in links:
        link_txt = await link.inner_text()
        if "excel" in link_txt.lower() or "baixar" in link_txt.lower():
            s3_url_holder = {}
            dl_ready = asyncio.Event()

            async def route_handler(route, request=None):
                u = route.request.url
                if ("s3.amazonaws.com" in u or "s3.us-east" in u) and not dl_ready.is_set():
                    s3_url_holder["url"] = u
                    dl_ready.set()
                try:
                    await route.continue_()
                except Exception:
                    pass

            await context.route("**/*", route_handler)
            await link.click()
            try:
                await asyncio.wait_for(dl_ready.wait(), timeout=30)
            finally:
                await context.unroute("**/*", route_handler)

            if "url" in s3_url_holder:
                async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                    resp = await client.get(s3_url_holder["url"])
                caminho.write_bytes(resp.content)

                if _is_valid_excel(caminho):
                    return caminho
                else:
                    caminho.unlink(missing_ok=True)
                    raise Exception(
                        f"Download do relatÃ³rio '{nome}' retornou arquivo invÃ¡lido. "
                        f"Acesse o GW, clique em 'Gerar' no relatÃ³rio '{nome}' e tente importar novamente."
                    )
            raise Exception(f"URL S3 nÃ£o capturada para '{nome}'")

    raise Exception(f"BotÃ£o 'Baixar Excel' nÃ£o encontrado para o relatÃ³rio '{nome}'.")

def _fmt_data(val) -> str:
    """Formata data para DD/MM/AAAA â€" aceita Timestamp ou serial Excel"""
    if pd.isna(val):
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%d/%m/%Y")
    try:
        return (datetime(1899, 12, 30) + timedelta(days=int(val))).strftime("%d/%m/%Y")
    except:
        return str(val)

def processar_dataframes(path1: Path, path2: Path) -> list[dict]:
    """Cruza os dois Excels e retorna lista de faturas prontas"""
    _debug_auto: list[str] = []

    # Le Excel 1 - dados principais (8 colunas do GW)
    df1_raw = pd.read_excel(path1, skiprows=1)
    _debug_auto.append(f"path1: {path1.name} | shape: {df1_raw.shape} | cols: {list(df1_raw.columns)[:8]}")

    df1 = df1_raw.copy()
    df1.columns = [
        "numero", "emissao", "vencimento", "filial",
        "valor", "cliente_nome", "cliente_cnpj", "situacao"
    ][:len(df1.columns)] + list(df1.columns[8:]) if len(df1.columns) > 8 else [
        "numero", "emissao", "vencimento", "filial",
        "valor", "cliente_nome", "cliente_cnpj", "situacao"
    ][:len(df1.columns)]

    _debug_auto.append(f"linhas brutas (apos skiprows): {len(df1)}")
    if len(df1) > 0:
        _debug_auto.append(f"amostra primeira linha: {dict(df1.iloc[0])}")

    # Filtra canceladas e linhas sem numero
    if "situacao" in df1.columns:
        antes = len(df1)
        df1 = df1[df1["situacao"].astype(str).str.strip() != "Cancelada"].copy()
        _debug_auto.append(f"apos filtro 'Cancelada': {antes} -> {len(df1)}")
    if "numero" in df1.columns:
        antes = len(df1)
        df1 = df1.dropna(subset=["numero"])
        _debug_auto.append(f"apos dropna numero: {antes} -> {len(df1)}")
    processar_dataframes._last_debug_auto = _debug_auto

    # Converte datas
    df1["emissao_fmt"]    = df1["emissao"].apply(_fmt_data)
    df1["vencimento_fmt"] = df1["vencimento"].apply(_fmt_data)

    # Normaliza número: "005049/2026" → "005049", "5049.0" → "005049"
    df1["numero"] = (
        df1["numero"].astype(str).str.strip()
        .str.split("/").str[0]      # remove "/2026"
        .str.split(".").str[0]      # remove ".0" de float
        .str.zfill(6)
    )

    # Lê Excel 2 – chaves de acesso (Complemento Operações)
    # dtype=str garante que a chave de 44 dígitos não seja truncada para notação científica
    chaves = None
    _debug_complemento: list[str] = []
    if path2 is not None:
        try:
            # Lê os headers reais primeiro (sem skiprows) para entender a estrutura
            df2_header = pd.read_excel(path2, nrows=1, dtype=str)
            _debug_complemento.append(f"Headers raw: {list(df2_header.columns)}")

            # Lê os dados pulando a primeira linha (título do relatório GW)
            df2 = pd.read_excel(path2, skiprows=1, dtype=str)
            _debug_complemento.append(f"Colunas lidas: {list(df2.columns)} ({len(df2)} linhas)")

            # --- Detecção automática de colunas ---
            chave_col = None
            numero_col = None

            # 1. Tenta pelo nome do cabeçalho (case-insensitive)
            for col in df2.columns:
                c = str(col).lower()
                if "chave" in c or "acesso" in c:
                    chave_col = col
                if ("fat" in c or "fatura" in c) and any(x in c for x in ["num", "nro", "nº", "n."]):
                    numero_col = col

            # 2. Tenta pelo conteúdo das colunas (44 dígitos = chave NF-e)
            if not chave_col:
                for col in df2.columns:
                    sample = df2[col].dropna().astype(str).str.strip()
                    if len(sample) > 0 and (sample.str.len() == 44).mean() > 0.3:
                        chave_col = col
                        break

            # 3. Fallback posicional (estrutura esperada: cte, emissao, chave, numero_fatura)
            if chave_col is None and len(df2.columns) >= 4:
                chave_col = df2.columns[2]
                _debug_complemento.append("chave_col: fallback posicional col[2]")

            if numero_col is None and len(df2.columns) >= 4:
                numero_col = df2.columns[3]
                _debug_complemento.append("numero_col: fallback posicional col[3]")

            _debug_complemento.append(f"chave_col={chave_col!r}  numero_col={numero_col!r}")

            if chave_col and numero_col:
                df2_work = df2[[chave_col, numero_col]].copy()
                df2_work.columns = ["chave", "numero_fatura"]
                df2_work["chave"] = df2_work["chave"].astype(str).str.strip()
                df2_work["numero_fatura"] = (
                    df2_work["numero_fatura"].astype(str).str.strip()
                    .str.split("/").str[0]   # remove "/2026"
                    .str.split(".").str[0]   # remove ".0" de float
                    .str.zfill(6)
                )
                df2_work = df2_work[df2_work["chave"].str.len() == 44]
                _debug_complemento.append(f"Chaves válidas (44 dígitos): {len(df2_work)}")

                if not df2_work.empty:
                    chaves = df2_work.groupby("numero_fatura")["chave"].first().reset_index()
                    chaves.columns = ["numero", "chave"]
                    _debug_complemento.append(f"Faturas com chave: {list(chaves['numero'])}")
                else:
                    _debug_complemento.append("Nenhuma chave válida de 44 dígitos encontrada")
            else:
                _debug_complemento.append("Colunas chave/numero_fatura não encontradas")

        except Exception as e:
            _debug_complemento.append(f"ERRO ao processar Complemento: {e}")
            chaves = None

    # Cruzamento (sem chaves se Complemento indisponível)
    if chaves is not None:
        resultado = df1.merge(chaves, on="numero", how="left")
    else:
        resultado = df1.copy()
        resultado["chave"] = ""

    # Guarda diagnóstico para consulta via /api/debug-complemento
    processar_dataframes._last_debug = _debug_complemento

    faturas = []
    for _, row in resultado.iterrows():
        faturas.append({
            "numero": row["numero"],
            "emissao": row["emissao_fmt"],
            "vencimento": row["vencimento_fmt"],
            "filial": row["filial"],
            "valor": round(float(row["valor"]) if pd.notna(row["valor"]) else 0, 2),
            "cliente_nome": str(row["cliente_nome"]).strip(),
            "cliente_cnpj": str(row["cliente_cnpj"]).strip(),
            "situacao": str(row["situacao"]).strip(),
            "chave": str(row["chave"]).strip() if pd.notna(row.get("chave")) else "",
            "factory_sugerida": "gc_sp" if "SP" in str(row["filial"]) else "gc_matriz",
        })

    return faturas

async def _buscar_faturas_via_consultafatura(user_id: int | None = None) -> list[dict]:
    """
    Fallback: quando o Excel 'Automação Operações' vem vazio (cache do GW
    pode estar travado), busca direto em /consultafatura?acao=consultar
    com filtro de emissão = hoje.
    Retorna lista no MESMO formato de processar_dataframes.
    """
    from _tz import now_br
    creds = get_credencial("gw", user_id=user_id)
    base = "https://webtrans.saas.gwsistemas.com.br"
    hoje = now_br().strftime("%d/%m/%Y")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        # Login GW
        await page.goto(f"{base}/login", wait_until="domcontentloaded", timeout=30000)
        await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
        await page.fill('input[name="login"]', creds["usuario"])
        await page.fill('input[name="senha"]', creds["senha"])
        await page.locator('button.button-login').click()
        try:
            await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        except Exception:
            pass
        # Acessa consultafatura — o wait_for do select abaixo já é a confirmação responsiva
        # de que a sessão GW estabilizou (se ainda estivesse carregando, o select não existiria).
        await page.goto(f"{base}/consultafatura?acao=iniciar", wait_until="domcontentloaded", timeout=30000)
        await page.locator('select[name="campoDeConsulta"]').wait_for(state="visible", timeout=15000)

        await page.select_option('select[name="campoDeConsulta"]', value="emissao_fatura")
        await page.fill('input[name="dtemissao1"]', hoje)
        await page.fill('input[name="dtemissao2"]', hoje)
        # filialId 0 = TODAS
        try:
            await page.select_option('select[name="filialId"]', value="0")
        except Exception:
            pass
        try:
            await page.select_option('select[name="finalizada"]', label="Todas")
        except Exception:
            pass
        try:
            await page.select_option('select[name="limiteResultados"]', value="200")
        except Exception:
            pass

        # Pesquisar — espera response
        try:
            async with page.expect_response(
                lambda r: "consultafatura" in r.url and "acao=consultar" in r.url,
                timeout=30000,
            ):
                await page.click('input[value="Pesquisar"]')
        except Exception:
            await page.click('input[value="Pesquisar"]')
        # Aguarda a tabela renderizar — sai imediatamente quando os checkboxes aparecem,
        # ou quando uma mensagem de "0 resultados" aparece. Sem sleep fixo.
        try:
            await page.wait_for_function(
                """() => {
                    if (document.querySelectorAll('input[id^="ck"]').length > 0) return true;
                    const t = (document.body && document.body.innerText) || '';
                    return /Nenhum (?:resultado|registro)|N[ãa]o h[áa] registros|sem resultados/i.test(t);
                }""",
                timeout=15000,
            )
        except Exception:
            pass

        # Debug: conta checkboxes e linhas
        debug_info = await page.evaluate("""() => {
            const ckboxes = document.querySelectorAll('input[id^="ck"]').length;
            const trs = document.querySelectorAll('tr').length;
            const links = document.querySelectorAll('a').length;
            const url = location.href;
            // Pega texto de "Total de" se existir
            const m = (document.body.innerText || '').match(/Total[^\\n]{0,80}/);
            return {ckboxes, trs, links, url: url.slice(0, 200), totalSnippet: m ? m[0] : ''};
        }""")
        _prog_log(f"  [fallback] checkboxes={debug_info.get('ckboxes')} trs={debug_info.get('trs')} url={debug_info.get('url')[:80]}")
        if debug_info.get('totalSnippet'):
            _prog_log(f"  [fallback] total: {debug_info['totalSnippet'][:120]}")

        # Parsea linhas: cada linha que tem checkbox ck* + número fatura X/AAAA é uma fatura.
        # Padrão simples e tolerante (sem exigir contagem mínima de tds).
        faturas_dom = await page.evaluate(
            """() => {
                function parseValor(t) {
                    if (!t) return 0;
                    const s = String(t).replace(/[^0-9.,-]/g,'').replace(/\\./g,'').replace(',', '.');
                    return parseFloat(s) || 0;
                }
                const out = [];
                for (const tr of document.querySelectorAll('tr')) {
                    if (!tr.querySelector('input[id^=\\"ck\\"]')) continue;
                    const linhaTxt = tr.textContent || '';
                    // Numero fatura
                    const numMatch = linhaTxt.match(/(\\d{5,6})\\/(\\d{4})/);
                    if (!numMatch) continue;
                    const numero = numMatch[1].padStart(6, '0');
                    // Datas (todas no formato DD/MM/AAAA)
                    const datas = [...linhaTxt.matchAll(/(\\d{2}\\/\\d{2}\\/\\d{4})/g)].map(m => m[1]);
                    // Valores monetários brasileiros (com vírgula). O GW exibe múltiplas
                    // colunas (Valor, Saldo, Multa, Juros, Desconto). Para faturas
                    // "Descontadas" o saldo vira 0,00 mas o valor original continua na
                    // linha — então pegamos o MAIOR valor para representar o valor da
                    // fatura (zeros e descontos são sempre menores).
                    //
                    // Regex aceita 2 formatos: "3.678,40" (com separador de milhar) e
                    // "3678,40" (sem). Antes exigia o ponto de milhar, truncando
                    // valores >= 1000 sem ponto (ex: "3678,40" virava "678,40").
                    const valoresStr = [...linhaTxt.matchAll(/(\\d+(?:\\.\\d{3})*,\\d{2})/g)].map(m => m[1]);
                    const valoresNum = valoresStr.map(parseValor);
                    const valor = valoresNum.length ? Math.max(...valoresNum) : 0;
                    // Lê todas as TDs com texto trimado — usado pra detectar
                    // filial, cliente e situação por coluna específica (mais
                    // confiável que regex sobre o texto inteiro da linha).
                    const tds = [...tr.querySelectorAll('td')]
                        .map(td => (td.textContent || '').trim().replace(/\\[\\.\\.\\.\\]/g, '').trim());

                    // Extrai número do lote da linha (formato típico: "Lote: 2547").
                    // Usado tanto no filtro do cliente quanto no fallback de filial.
                    const mLote = linhaTxt.match(/Lote\\s*:?\\s*(\\d+)/i);
                    const numLote = mLote ? parseInt(mLote[1]) : 0;

                    // Filial: detecta MATRIZ, "Filial SP", "Filial BA" e qualquer
                    // outro "Filial XX". Empresa tem múltiplas filiais (não só
                    // MATRIZ + SP — também BA descoberta no cache real).
                    let filial = '';
                    for (const t of tds) {
                        if (/^MATRIZ$/i.test(t)) { filial = 'MATRIZ'; break; }
                        const mFil = t.match(/^Filial\\s+(.+)$/i);
                        if (mFil) {
                            const sigla = mFil[1].trim().toUpperCase();
                            filial = `Filial ${sigla}`;
                            break;
                        }
                        if (/^S[ãa]o\\s*Paulo$/i.test(t)) { filial = 'Filial SP'; break; }
                    }
                    // Fallback 1: regex no texto inteiro (caso a TD tenha texto extra).
                    if (!filial) {
                        const mFil = linhaTxt.match(/Filial\\s+(SP|BA|RJ|MG|PR|RS|SC|GO|DF|BSB)/i);
                        if (mFil) filial = `Filial ${mFil[1].toUpperCase()}`;
                        else if (/\\bMATRIZ\\b/i.test(linhaTxt)) filial = 'MATRIZ';
                    }
                    // Fallback 2: inferência pelo número do lote.
                    // Mapeamento empírico (CLAUDE.md): 2547=MATRIZ, 2548=SP.
                    // Lotes desconhecidos ficam como MATRIZ (default conservador).
                    if (!filial && numLote > 0) {
                        if (numLote === 2547) filial = 'MATRIZ';
                        else if (numLote === 2548) filial = 'Filial SP';
                        // Outros lotes: deixa cair no default abaixo
                    }
                    if (!filial) filial = 'MATRIZ';

                    // Cliente: procura primeira TD que tenha letras (>= 1 letra),
                    // tamanho > 4, e que NÃO seja: número de fatura, data, lote,
                    // valor, filial, situação. Aceita clientes que começam com
                    // dígito (ex: "3M DO BRASIL", "4 RODAS PNEUS") desde que
                    // tenham letras de fato (não TD com "fatura/ano + Lote").
                    let cliente = '';
                    for (const t of tds) {
                        if (t.length < 4) continue;
                        if (!/[A-Za-zÀ-ÿ]/.test(t)) continue;  // sem letra = pula (números puros)
                        if (/^\\d{5,6}\\/?\\d{0,4}\\b/.test(t)) continue;  // começa com n° fatura/ano
                        if (/Lote\\s*:?\\s*\\d/i.test(t)) continue;  // contém "Lote: 2547"
                        if (/^Lote/i.test(t)) continue;  // td só com "Lote"
                        if (/^\\d{2}\\/\\d{2}\\/\\d{4}/.test(t)) continue;  // data
                        if (/^\\d{1,3}(?:\\.\\d{3})*,\\d{2}$/.test(t)) continue;  // valor
                        if (/^MATRIZ$|^Filial\\s+/i.test(t)) continue;
                        if (/^S[ãa]o\\s*Paulo$/i.test(t)) continue;
                        if (/^(Em Aberto|Cancelad[ao]?|Descontad[ao]?|Normal|Sim|Não)$/i.test(t)) continue;
                        cliente = t;
                        break;
                    }

                    // Situação: procura td com texto exato dos status conhecidos
                    let sit = 'Em Aberto';
                    for (const t of tds) {
                        if (/^Cancelad/i.test(t)) { sit = 'Cancelada'; break; }
                        if (/^Descontad/i.test(t)) { sit = 'Descontada (Factoring)'; break; }
                        if (/^Em Aberto$/i.test(t)) { sit = 'Em Aberto'; break; }
                        if (/^Normal$/i.test(t)) { sit = 'Normal'; break; }
                    }

                    out.push({
                        numero,
                        emissao: datas[0] || '',
                        vencimento: datas[1] || datas[0] || '',
                        valor,
                        cliente_nome: cliente,
                        situacao: sit,
                        filial,
                        // Debug: amostra estruturada da linha pra investigação
                        _debug_tds: tds.slice(0, 20),  // até 20 colunas com texto
                        _debug_valores: valoresStr,
                    });
                }
                return out;
            }"""
        )
        # Log diagnóstico — distribuição por filial e situação detectadas.
        from collections import Counter
        if faturas_dom:
            por_filial = Counter(f.get("filial", "?") for f in faturas_dom)
            por_situacao = Counter(f.get("situacao", "?") for f in faturas_dom)
            por_valor = Counter("zero" if f.get("valor", 0) == 0 else "ok" for f in faturas_dom)
            _prog_log(f"  [fallback] distribuição filial: {dict(por_filial)}")
            _prog_log(f"  [fallback] distribuição situação: {dict(por_situacao)}")
            _prog_log(f"  [fallback] valores: {dict(por_valor)}")

            # Diagnóstico do valor zerado: lista cada TD separadamente.
            # Isso revela em qual coluna o valor original está no GW —
            # impossível inferir só do textContent concatenado.
            zeros = [f for f in faturas_dom if f.get("valor", 0) == 0][:3]
            naozeros = [f for f in faturas_dom if f.get("valor", 0) > 0][:1]
            for f in zeros:
                _prog_log(f"  [debug VALOR=0] fatura {f.get('numero')} (sit={f.get('situacao')}):")
                _prog_log(f"     valores no regex: {f.get('_debug_valores')}")
                for i, td in enumerate(f.get("_debug_tds", [])):
                    _prog_log(f"     td[{i}]: {td!r}")
            for f in naozeros:
                _prog_log(f"  [debug VALOR>0 ref] fatura {f.get('numero')} (sit={f.get('situacao')}, val={f.get('valor')}):")
                _prog_log(f"     valores no regex: {f.get('_debug_valores')}")
                for i, td in enumerate(f.get("_debug_tds", [])):
                    _prog_log(f"     td[{i}]: {td!r}")

        # Remove campos de debug antes de retornar (não persistir no cache)
        for f in faturas_dom:
            f.pop("_debug_tds", None)
            f.pop("_debug_valores", None)

        await browser.close()
        return faturas_dom


async def processar_excels(user_id: int | None = None) -> list[dict]:
    """Entry point: baixa relatórios do GW e processa.

    Estratégia em 2 estágios:
      1. Tenta baixar os relatórios personalizados ("Automação"/"Complemento") —
         caminho rápido com chaves NF-e prontas.
      2. Se isso falhar (relatório não cadastrado, vazio, ou exception qualquer),
         cai no fallback /consultafatura que sabidamente funciona em qualquer
         ambiente sem depender de relatório customizado.
    """
    global _cache_faturas
    _prog_reset()
    path1: Path | None = None
    path2: Path | None = None
    erro_relatorio: str | None = None

    try:
        _prog_log("🚀 Iniciando download dos relatórios do GW...")
        try:
            path1, path2 = await baixar_relatorios_gw(user_id=user_id)
            _prog_log("📊 Processando planilhas...")
            _cache_faturas = processar_dataframes(path1, path2)
        except Exception as e:
            # Relatório não encontrado, GW indisponível, parser falhou, etc.
            # Não aborta — o fallback /consultafatura cobre esse caso.
            erro_relatorio = str(e)
            _prog_log(f"⚠️ Não foi possível usar relatórios personalizados: {erro_relatorio[:200]}")
            _cache_faturas = []

        # FALLBACK: dispara quando o relatório falhou OU veio vazio.
        # /consultafatura funciona em qualquer instalação do GW (não depende
        # de relatório customizado por usuário).
        if not _cache_faturas:
            if erro_relatorio:
                _prog_log("🔄 Buscando faturas via /consultafatura (relatório indisponível)...")
            else:
                _prog_log("⚠️ Relatório personalizado vazio — usando fallback /consultafatura")
            try:
                fallback = await _buscar_faturas_via_consultafatura(user_id=user_id)
            except Exception as e:
                _prog_log(f"❌ Fallback /consultafatura também falhou: {e}")
                fallback = []

            # Filtro de situação: só carrega faturas Descontada (Factoring) ou Normal.
            # Outras situações (Em Aberto, Cancelada, Quitada, etc.) não são
            # operáveis pelo AutoFactory — não fazem sentido aparecer na lista.
            antes_filtro = len(fallback)
            fallback = [
                f for f in fallback
                if f.get("situacao", "") in ("Descontada (Factoring)", "Normal")
            ]
            if antes_filtro != len(fallback):
                _prog_log(f"  [fallback] filtradas {antes_filtro - len(fallback)} fatura(s) de outras situações ({antes_filtro} → {len(fallback)})")

            # Cruza com chaves do Complemento (se o Complemento foi baixado
            # com sucesso na etapa 1; se não, fica sem chave NF-e — é apenas
            # uma melhoria de UX, não bloqueia a operação).
            chaves_map: dict[str, str] = {}
            try:
                if path2:
                    df2 = pd.read_excel(path2, skiprows=1, dtype=str)
                    if len(df2.columns) >= 4:
                        for _, row in df2.iterrows():
                            num = str(row.iloc[3] or "").strip().split("/")[0].split(".")[0].zfill(6)
                            ch = str(row.iloc[2] or "").strip()
                            if num and ch and len(ch) == 44 and num not in chaves_map:
                                chaves_map[num] = ch
            except Exception:
                pass

            # Monta lista no formato esperado.
            # factory_sugerida: SP→gc_sp, BA/MATRIZ/outras→gc_matriz (default).
            # Filiais não-mapeadas caem em gc_matriz mas o usuário pode reatribuir
            # manualmente no UI antes de executar.
            faturas_final = []
            for f in fallback:
                num = f["numero"]
                filial = f.get("filial") or "MATRIZ"
                is_sp = "SP" in filial.upper().replace("FILIAL ", "")
                faturas_final.append({
                    "numero": num,
                    "emissao": f.get("emissao", ""),
                    "vencimento": f.get("vencimento", ""),
                    "filial": filial,
                    "valor": f.get("valor", 0),
                    "cliente_nome": f.get("cliente_nome", ""),
                    "cliente_cnpj": "",
                    "situacao": f.get("situacao", "Em Aberto"),
                    "chave": chaves_map.get(num, ""),
                    "factory_sugerida": "gc_sp" if is_sp else "gc_matriz",
                })
            _cache_faturas = faturas_final
            _prog_log(f"   {len(_cache_faturas)} fatura(s) recuperadas via fallback")

        # Se chegou aqui sem nada, agora sim falha duro (sem caminho viável)
        if not _cache_faturas:
            msg = erro_relatorio or "Nenhuma fatura encontrada (relatório vazio e fallback sem resultados)"
            raise Exception(msg)

        _salvar_cache(_cache_faturas)   # persiste em disco
        _prog_log(f"✅ {len(_cache_faturas)} fatura(s) carregada(s)")
        _prog_finalizar(True)
        return _cache_faturas
    except Exception as e:
        _prog_finalizar(False, str(e))
        raise


