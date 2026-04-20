import asyncio
import json
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import os

from playwright.async_api import async_playwright
from config_manager import get_credencial

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "automacao_factory"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Arquivo de persistência da cache (sobrevive a reinicializações do backend)
_CACHE_FILE = DOWNLOAD_DIR / "cache_faturas.json"

# Cache global para uso nos módulos de automação
_cache_faturas: list[dict] = []

def _salvar_cache(faturas: list[dict]):
    """Persiste a cache em disco para sobreviver a reinicializações."""
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

# Tenta carregar cache persistida ao importar o módulo
_cache_faturas = _carregar_cache()

def serial_excel_para_data(serial) -> str:
    """Converte número serial do Excel para data string DD/MM/AAAA"""
    if pd.isna(serial):
        return ""
    try:
        data = datetime(1899, 12, 30) + timedelta(days=int(serial))
        return data.strftime("%d/%m/%Y")
    except:
        return str(serial)

async def _gerar_relatorio_personalizado(page, nome_relatorio: str, data_hoje: str, base: str, preencher_data: bool = True):
    """
    Abre Relatórios > Financeiro > Contas a Receber, seleciona o relatório
    personalizado pelo nome, preenche data de emissão = hoje e clica em Gerar.

    Estrutura observada no GW:
    - Filtro "Emissão" (Automação) ou "Emissão Fatura" (Complemento)
    - Dropdown "Entre" seguido de dois inputs de data [início] [fim]

    preencher_data=False: não preenche o filtro de data (usa configuração salva no relatório).
    Usar False para o Complemento, pois a data do CT-e pode diferir da data da fatura.
    """
    url_rel = f"{base}/relcontasreceber?acao=iniciar&modulo=webtrans"
    await page.goto(url_rel, wait_until="networkidle")
    await page.wait_for_timeout(1500)

    # Clica na aba "Relatórios Personalizados"
    aba = await page.query_selector("text=Relatórios Personalizados")
    if aba:
        await aba.click()
        await page.wait_for_timeout(1000)

    # Seleciona o radio button do relatório pelo nome
    # Cada radio fica numa <td> junto com o texto (sem <label>)
    selecionado = await page.evaluate(f"""
        () => {{
            const nome = {repr(nome_relatorio.lower())};
            const tds = [...document.querySelectorAll('td')];
            for (const td of tds) {{
                if (td.textContent.trim().toLowerCase().includes(nome)) {{
                    const radio = td.querySelector('input[type="radio"]')
                               || td.previousElementSibling?.querySelector('input[type="radio"]');
                    if (radio) {{ radio.click(); return true; }}
                }}
            }}
            // fallback: percorre todos os radios e verifica o texto próximo
            const radios = [...document.querySelectorAll('input[type="radio"]')];
            for (const r of radios) {{
                const row = r.closest('tr') || r.parentElement;
                if (row && row.textContent.toLowerCase().includes(nome)) {{
                    r.click(); return true;
                }}
            }}
            return false;
        }}
    """)

    await page.wait_for_timeout(800)  # aguarda atualizar colunas/filtros do relatório selecionado

    if preencher_data:
        # Preenche os dois campos de data do filtro de Emissão (início e fim = hoje)
        # A linha do filtro contém "Emissão" no label e dois inputs de texto para as datas
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

    # Garante formato Excel selecionado (primeiro radio após os de relatório = XLS)
    await page.evaluate("""
        () => {
            // O radio do Excel fica logo acima do botão Gerar — é o primeiro radio da linha de formato
            const btn = document.querySelector('input[value="Gerar Relatório"]');
            if (!btn) return;
            const linhaFormato = btn.closest('tr')?.previousElementSibling;
            if (linhaFormato) {
                const radios = linhaFormato.querySelectorAll('input[type="radio"]');
                if (radios[0]) radios[0].click(); // primeiro = Excel
            }
        }
    """)

    # Clica em "Gerar Relatório"
    await page.click('input[value="Gerar Relatório"]')
    await page.wait_for_timeout(8000)  # aguarda GW processar e salvar em Meus Relatórios


async def baixar_relatorios_gw() -> tuple[Path, Path | None]:
    """
    Gera os relatórios personalizados no GW com data de emissão = hoje
    e depois baixa de Meus Relatórios.
    """
    creds = get_credencial("gw")
    base = "https://webtrans.saas.gwsistemas.com.br"
    hoje = datetime.now().strftime("%d/%m/%Y")
    meus_rel_url = f"{base}/RelatorioControlador?acao=abrirTelaMeusRelatorios"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        # Login
        await page.goto(f"{base}/login", wait_until="domcontentloaded")
        await page.wait_for_selector('#login', timeout=15000)
        await page.fill('#login', creds["usuario"])
        await page.fill('#senha', creds["senha"])
        await page.click('.button-login')
        await page.wait_for_load_state("networkidle", timeout=30000)

        # Gera "Automação Operações - Jonathas" com data de hoje
        await _gerar_relatorio_personalizado(page, "Automação Operações", hoje, base)

        # Baixa de Meus Relatórios
        await page.goto(meus_rel_url, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        arquivo1 = await _baixar_meu_relatorio(page, context, "Automação Operações", meus_rel_url)

        # Gera "Complemento Operações" com filtro de data = hoje (mesma lógica do Automação).
        # O filtro é por data de emissão da fatura.
        # Aguarda 15s (Complemento é maior e o GW pode demorar mais para processar).
        arquivo2 = None
        try:
            await _gerar_relatorio_personalizado(page, "Complemento Operações", hoje, base, preencher_data=True)
            # Aguarda mais 7s extras além dos 8s já esperados dentro da função
            await page.wait_for_timeout(7000)
            await page.goto(meus_rel_url, wait_until="networkidle")
            await page.wait_for_timeout(2000)
            arquivo2 = await _baixar_meu_relatorio(page, context, "Complemento Operações", meus_rel_url)
        except Exception as e:
            # Loga o erro para diagnóstico — sem chave a digitação prossegue, mas sem chave NF
            import logging
            logging.warning(f"[Complemento] Falha ao baixar: {e}. Chave de acesso ficará vazia.")

        await browser.close()
        return arquivo1, arquivo2

def _is_valid_excel(path: Path) -> bool:
    """Verifica se o arquivo é um Excel real (magic bytes PK = zip/xlsx)"""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        return magic == b"PK\x03\x04"
    except Exception:
        return False


async def _baixar_meu_relatorio(page, context, nome: str, url: str = None) -> Path:
    """Clica em 'Baixar Excel' para o relatório mais recente com o nome dado"""
    prefixo = nome.replace(' ', '_')
    caminho = DOWNLOAD_DIR / f"{prefixo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    import unicodedata
    import httpx

    def norm(s):
        return unicodedata.normalize("NFC", s).lower()

    # Busca a linha do relatório na tabela
    rows = await page.query_selector_all("tr")
    linha_alvo = None
    for row in rows:
        txt = await row.inner_text()
        if norm(nome) in norm(txt):
            linha_alvo = row
            break

    if not linha_alvo:
        raise Exception(
            f"Relatório '{nome}' não encontrado em Meus Relatórios. "
            "Gere-o manualmente no GW (Relatórios > Meus Relatórios) e tente novamente."
        )

    # Tenta clicar em "Gerar" / "Atualizar" para garantir link S3 fresco
    links_linha = await linha_alvo.query_selector_all("a, button")
    for el in links_linha:
        el_txt = (await el.inner_text()).lower()
        if "gerar" in el_txt or "atualizar" in el_txt or "processar" in el_txt:
            await el.click()
            await page.wait_for_timeout(8000)  # aguarda o GW processar o relatório
            # Recarrega a lista de relatórios para pegar o link de download atualizado
            await page.goto(
                f"https://webtrans.saas.gwsistemas.com.br/RelatorioControlador?acao=abrirTelaMeusRelatorios",
                wait_until="networkidle"
            )
            await page.wait_for_timeout(2000)
            # Re-localiza a linha após reload
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
                        f"Download do relatório '{nome}' retornou arquivo inválido. "
                        f"Acesse o GW, clique em 'Gerar' no relatório '{nome}' e tente importar novamente."
                    )
            raise Exception(f"URL S3 não capturada para '{nome}'")

    raise Exception(f"Botão 'Baixar Excel' não encontrado para o relatório '{nome}'.")

def _fmt_data(val) -> str:
    """Formata data para DD/MM/AAAA — aceita Timestamp ou serial Excel"""
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
    # Lê Excel 1 — dados principais (8 colunas do GW)
    df1 = pd.read_excel(path1, skiprows=1)
    df1.columns = [
        "numero", "emissao", "vencimento", "filial",
        "valor", "cliente_nome", "cliente_cnpj", "situacao"
    ]

    # Filtra canceladas e linhas sem número
    df1 = df1[df1["situacao"].astype(str).str.strip() != "Cancelada"].copy()
    df1 = df1.dropna(subset=["numero"])

    # Converte datas
    df1["emissao_fmt"]    = df1["emissao"].apply(_fmt_data)
    df1["vencimento_fmt"] = df1["vencimento"].apply(_fmt_data)

    # Formata número
    df1["numero"] = df1["numero"].astype(str).str.strip().str.split(".").str[0].str.zfill(6)

    # Lê Excel 2 — chaves de acesso (opcional: pode estar indisponível)
    # IMPORTANTE: dtype=str garante que a chave de 44 dígitos não seja truncada
    # para notação científica (float64 só tem ~15 dígitos de precisão)
    chaves = None
    if path2 is not None:
        try:
            df2 = pd.read_excel(path2, skiprows=1, dtype=str)
            df2.columns = ["cte", "emissao_fatura", "chave", "numero_fatura"]
            df2["numero_fatura"] = df2["numero_fatura"].str.strip().str.split(".").str[0].str.zfill(6)
            df2 = df2.dropna(subset=["chave"])
            # Remove espaços e garante que a chave tem exatamente 44 caracteres
            df2["chave"] = df2["chave"].str.strip()
            df2 = df2[df2["chave"].str.len() == 44]
            chaves = df2.groupby("numero_fatura")["chave"].first().reset_index()
            chaves.columns = ["numero", "chave"]
        except Exception:
            chaves = None

    # Cruzamento (sem chaves se Complemento indisponível)
    if chaves is not None:
        resultado = df1.merge(chaves, on="numero", how="left")
    else:
        resultado = df1.copy()
        resultado["chave"] = ""

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
            "factory_sugerida": "firma_sp" if "SP" in str(row["filial"]) else "firma_matriz",
        })

    return faturas

async def processar_excels() -> list[dict]:
    """Entry point: baixa relatórios do GW e processa"""
    global _cache_faturas
    path1, path2 = await baixar_relatorios_gw()
    _cache_faturas = processar_dataframes(path1, path2)
    _salvar_cache(_cache_faturas)   # persiste em disco
    return _cache_faturas

def processar_excels_local(path1: Path, path2: Path) -> list[dict]:
    """Para testes com arquivos locais (sem precisar do GW)"""
    return processar_dataframes(path1, path2)
