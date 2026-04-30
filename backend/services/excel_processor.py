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
    _progresso["inicio"] = datetime.now().isoformat()
    _progresso["fim"] = None

def _prog_log(msg: str):
    _progresso["logs"].append(msg)

def _prog_finalizar(ok: bool, erro: str | None = None):
    _progresso["status"] = "concluido" if ok else "erro"
    _progresso["fim"] = datetime.now().isoformat()
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
        await page.goto(meus_rel_url, wait_until="load", timeout=60000)
        await page.wait_for_timeout(2000)
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
            await page.wait_for_timeout(5000)

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


    # Sempre navega para garantir estado limpo
    url_rel = f"{base}/relcontasreceber?acao=iniciar&modulo=webtrans"
    await page.goto(url_rel, wait_until="load", timeout=60000)
    await page.wait_for_timeout(1500)

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
        await page.wait_for_timeout(2000)

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

    await page.wait_for_timeout(1000)  # aguarda filtros do relatório selecionado atualizarem

    if preencher_data:
        # Preenche os dois campos de data do filtro de EmissÃ£o (inÃ­cio e fim = hoje)
        # A linha do filtro contÃ©m "EmissÃ£o" no label e dois inputs de texto para as datas
        await page.evaluate(f"""
            () => {{
                const hoje = {repr(data_hoje)};
                // Encontra a linha da tabela que tem "emiss" no texto (label do filtro)
                const trs = [...document.querySelectorAll('tr')];
                for (const tr of trs) {{
                    const textoLinha = tr.textContent.toLowerCase();
                    if (textoLinha.includes('emiss')) {{
                        const inputs = [...tr.querySelectorAll('input[type="text"]')];
                        let preencheu = 0;
                        for (const inp of inputs) {{
                            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            nativeInputValueSetter.call(inp, hoje);
                            inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                            inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                            preencheu++;
                            if (preencheu >= 2) break;
                        }}
                        if (preencheu > 0) return preencheu;
                    }}
                }}
                return 0;
            }}
        """)

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
        # Aguarda o botão estar disponível na página antes de clicar
        await page.wait_for_timeout(500)
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
            await popup.wait_for_timeout(2000)
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

    await page.wait_for_timeout(3000)


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
        await page.wait_for_load_state("load", timeout=60000)
        await page.wait_for_timeout(3000)

        # Verifica se ainda está na tela de login (credenciais erradas ou problema no login)
        current_url = page.url
        if "login" in current_url.lower():
            raise Exception("Login GW falhou. Verifique as credenciais em Configurações.")

        # Aguarda a home carregar para garantir sessão ativa antes de navegar
        await page.goto(f"{base}/home", wait_until="load", timeout=60000)
        await page.wait_for_timeout(1500)

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
            await page.wait_for_timeout(8000)  # aguarda o GW processar o relatÃ³rio
            # Recarrega a lista de relatÃ³rios para pegar o link de download atualizado
            await page.goto(
                f"https://webtrans.saas.gwsistemas.com.br/RelatorioControlador?acao=abrirTelaMeusRelatorios",
                wait_until="load", timeout=60000
            )
            await page.wait_for_timeout(2000)
            # Re-localiza a linha apÃ³s reload
            rows = await page.query_selector_all("tr")
            for row in rows:
                txt = await row.inner_text()
                if norm(nome) in norm(txt):
                    linha_alvo = row
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

async def processar_excels(user_id: int | None = None) -> list[dict]:
    """Entry point: baixa relatÃ³rios do GW e processa"""
    global _cache_faturas
    _prog_reset()
    try:
        _prog_log("🚀 Iniciando download dos relatórios do GW...")
        path1, path2 = await baixar_relatorios_gw(user_id=user_id)
        _prog_log("📊 Processando planilhas...")
        _cache_faturas = processar_dataframes(path1, path2)
        _salvar_cache(_cache_faturas)   # persiste em disco
        _prog_log(f"✅ {len(_cache_faturas)} fatura(s) carregada(s)")
        _prog_finalizar(True)
        return _cache_faturas
    except Exception as e:
        _prog_finalizar(False, str(e))
        raise


