import asyncio
import time
import zipfile
import io
import re
import traceback
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, Page, BrowserContext
from browser_config import launch_kwargs
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

# URLs / textos típicos de telas que bloqueiam um usuário GW recém-criado
_FIRST_LOGIN_URL_MARKERS = (
    "trocar-senha", "troca-senha", "trocarSenha", "alterarSenha",
    "primeiro-acesso", "primeiroAcesso",
    "aceitar-termo", "aceitarTermo", "termo-uso",
    "redefinir", "expirou",
)
_FIRST_LOGIN_TEXT_MARKERS = (
    "troca de senha",
    "primeiro acesso",
    "aceitar os termos",
    "aceite o termo",
    "termo de uso",
    "senha expirada",
    "redefinir senha",
    "altere sua senha",
)

async def _diagnosticar_pos_login(page: Page) -> None:
    """
    Detecta telas de bloqueio que aparecem após o primeiro login de um
    usuário GW recém-criado (troca de senha obrigatória, aceite de termo).
    """
    url_low = page.url.lower()
    for m in _FIRST_LOGIN_URL_MARKERS:
        if m.lower() in url_low:
            raise Exception(
                f"Usuário GW está em fluxo de primeiro acesso (URL: {page.url}). "
                "Faça login manual no GW pelo menos uma vez para concluir "
                "troca de senha/aceite de termo, depois rode a automação novamente."
            )
    try:
        body_text = (await page.inner_text("body", timeout=2000)).lower()
    except Exception:
        return
    for marker in _FIRST_LOGIN_TEXT_MARKERS:
        if marker in body_text:
            raise Exception(
                f"Tela de bloqueio detectada após login GW: '{marker}'. "
                "Faça login manual no GW para concluir cadastro/troca de senha/"
                "aceite de termo, depois rode a automação novamente."
            )


async def _login_gw(page: Page, user_id: int | None = None):
    creds = get_credencial("gw", user_id=user_id)
    await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded", timeout=30000)
    # Aguarda campos de login renderizarem (mais confiável que timeout fixo)
    await page.locator('input[name="login"]').wait_for(state="visible", timeout=10000)
    await page.locator('input[name="login"]').fill(creds["usuario"])
    await page.locator('input[name="senha"]').fill(creds["senha"])
    await page.locator('button.button-login').click()
    # Aguarda redirect para fora do login — sai tão logo a URL muda
    try:
        await page.wait_for_url(lambda url: "login" not in url.lower(), timeout=30000)
    except Exception:
        if "login" in page.url.lower():
            raise Exception("Login GW falhou — verifique as credenciais em Configurações.")

    # Detecta telas de primeiro acesso que ficam fora de /login mas bloqueiam navegação
    await _diagnosticar_pos_login(page)

    # Garante que chegamos na home (o GW pode redirecionar por etapas)
    if "/home" not in page.url:
        await page.goto(f"{BASE_GW}/home", wait_until="load", timeout=30000)
    # Aguarda a home inicializar a sessão Java no servidor (networkidle com fallback)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)

    # Reverifica após o /home (alguns redirecionamentos só acontecem nessa etapa)
    await _diagnosticar_pos_login(page)


# ─── DIAGNÓSTICO DE BUSCA CTE ────────────────────────────────────────────────

# Marcadores que indicam que o servidor barrou a busca por permissão/sessão
_BUSCA_CTE_ERRO_MARKERS = (
    "sem permiss", "acesso negado", "não autorizado", "nao autorizado",
    "sessão expirada", "sessao expirada",
    "usuário sem perfil", "usuario sem perfil",
    "filial não dispon", "filial nao dispon",
    "perfil insuficiente",
)


async def _salvar_screenshot_debug(page: Page, prefix: str) -> str | None:
    """Salva screenshot em backend/debug/. Retorna o caminho ou None em falha."""
    try:
        debug_dir = Path(__file__).resolve().parent.parent / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = debug_dir / f"{prefix}-{stamp}.png"
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


async def _aguardar_busca_cte(
    page: Page,
    occ_antes: str,
    numero: str,
    log,
    max_seconds: int = 120,
    poll_seconds: float = 0.4,
    log_every_seconds: float = 15.0,
) -> bool:
    """
    Aguarda a busca CT-e terminar de forma responsiva:
      - Polling no DOM a cada ~0.4s
      - Detecta múltiplos sinais de fim: "Total de Ocorrências: N" (com mudança),
        "Nenhum/sem resultados", URL com paginaResultados, ou resposta HTTP
      - Sai imediatamente quando algum sinal aparece (não espera ciclo fixo)
      - Se aparecer marcador de erro conhecido, levanta Exception
      - Se a sessão cair (URL volta pra /login), levanta Exception
      - Hard cap em max_seconds (default 120s = 2 min)
    Retorna True se encontrou o resultado; False se estourou o cap.
    """
    inicio = time.monotonic()
    last_log = 0.0
    while True:
        elapsed = time.monotonic() - inicio
        if elapsed >= max_seconds:
            return False

        if "login" in page.url.lower() and "CTeControlador" not in page.url:
            raise Exception("sessão GW caiu — página redirecionou pra /login durante a busca")

        try:
            estado = await page.evaluate(
                """(antes) => {
                    if (!document.body) return { ok: false, texto: '' };
                    const t = document.body.innerText || '';

                    // Sinal 1: "Total de Ocorrências: N" com valor diferente do anterior
                    const m1 = t.match(/Total de Ocorr.ncias:\\s*(\\d+)/);
                    if (m1 && m1[0] !== antes) return { ok: true, texto: m1[0], total: parseInt(m1[1]) };

                    // Sinal 2: variantes de "Ocorrências: N"
                    const m2 = t.match(/Ocorr.ncias:\\s*(\\d+)/);
                    if (m2 && m2[0] !== antes) return { ok: true, texto: m2[0], total: parseInt(m2[1]) };

                    // Sinal 3: URL contém paginaResultados (busca submeteu)
                    if (location && location.search && location.search.includes('paginaResultados')) {
                        return { ok: true, texto: 'URL paginaResultados', total: -1 };
                    }

                    // Sinal 4: mensagem de "0 resultados" / "Nenhum encontrado"
                    if (/Nenhum (?:resultado|registro|CT-?e)|N[ãa]o h[áa] (?:registros|resultados)|sem resultados|0 registros/i.test(t)) {
                        return { ok: true, texto: '0 resultados', total: 0 };
                    }

                    // Sinal 5: tabela de resultados visível com linhas
                    const tbodyVis = document.querySelectorAll('table tbody tr');
                    if (tbodyVis.length > 1) {
                        return { ok: true, texto: `tabela com ${tbodyVis.length} linhas`, total: -1 };
                    }

                    return { ok: false, texto: t.slice(0, 800) };
                }""",
                occ_antes,
            )
            if estado.get("ok"):
                if elapsed > 5:
                    log(f"    ✓ Resultado apareceu após {int(elapsed)}s ({estado.get('texto', '')})")
                return True

            t_low = (estado.get("texto") or "").lower()
            for marker in _BUSCA_CTE_ERRO_MARKERS:
                if marker in t_low:
                    raise Exception(f"GW respondeu com erro: '{marker}'")
        except Exception as e:
            if "GW respondeu com erro" in str(e) or "sessão GW caiu" in str(e):
                raise

        if elapsed - last_log >= log_every_seconds:
            log(f"    ⏳ Aguardando GW computar resultados... ({int(elapsed)}s, máx 5min)")
            last_log = elapsed

        await asyncio.sleep(poll_seconds)


async def _diagnosticar_busca_cte(page: Page, numero: str, filial_label: str) -> str:
    """Mensagem acionável quando a busca CTe falha."""
    info: list[str] = [f"URL: {page.url}"]
    try:
        info.append(f"título: {await page.title()}")
    except Exception:
        pass
    body_text = ""
    try:
        body_text = await page.inner_text("body", timeout=3000)
    except Exception:
        pass
    body_low = body_text.lower()

    for marker in _BUSCA_CTE_ERRO_MARKERS:
        if marker in body_low:
            info.append(f"detectado: '{marker}'")
            info.append("Provavelmente o usuário GW não tem permissão para o módulo "
                        "CT-e ou para a filial selecionada. Confirme com o admin do GW.")
            shot = await _salvar_screenshot_debug(page, f"cte-{numero}-permissao")
            if shot: info.append(f"screenshot: {shot}")
            return " | ".join(info)

    try:
        opcoes_filial = await page.evaluate(
            """() => {
                const s = document.querySelector('#filial');
                if (!s) return null;
                return [...s.options].map(o => o.text);
            }"""
        )
        if opcoes_filial is None:
            info.append("select #filial não encontrado na tela")
        elif filial_label not in opcoes_filial:
            info.append(f"#filial não tem a opção '{filial_label}' "
                        f"(disponíveis: {opcoes_filial}). Usuário GW pode não ter "
                        "acesso a essa filial.")
            shot = await _salvar_screenshot_debug(page, f"cte-{numero}-filial")
            if shot: info.append(f"screenshot: {shot}")
            return " | ".join(info)
    except Exception:
        pass

    if "login" in page.url.lower():
        info.append("redirecionado para /login — sessão GW caiu durante a busca")
        return " | ".join(info)

    snippet = body_text.strip().replace("\n", " ")[:300]
    info.append(f"trecho da página: '{snippet}'")
    shot = await _salvar_screenshot_debug(page, f"cte-{numero}-timeout")
    if shot: info.append(f"screenshot: {shot}")
    return " | ".join(info)


async def _baixar_cte_de_meus_relatorios(
    page: Page,
    context: BrowserContext,
    marcador_dt: datetime,
    log,
    max_seconds: int = 300,
) -> bytes | None:
    """
    Fallback quando o popup do CT-e não captura o PDF: vai em
    /RelatorioControlador?acao=abrirTelaMeusRelatorios e baixa o relatório
    Modelo 17 (CT-e) processado depois de marcador_dt.
    """
    import httpx as _httpx

    URL_REL = f"{BASE_GW}/RelatorioControlador?acao=abrirTelaMeusRelatorios"

    inicio = time.monotonic()
    while True:
        if time.monotonic() - inicio >= max_seconds:
            log(f"    Meus Relatórios: timeout de {max_seconds}s sem encontrar CT-e gerado")
            return None

        try:
            await page.goto(URL_REL, wait_until="load", timeout=30000)
        except Exception as e:
            log(f"    Erro ao abrir Meus Relatórios: {e}")
            await asyncio.sleep(10)
            continue
        await page.wait_for_timeout(1500)

        # Procura linha mais recente: Rotina contém "CT-e" e Status="Gerado"
        # com timestamp >= marcador_dt
        marcador_iso = marcador_dt.strftime("%Y-%m-%dT%H:%M")
        linha_idx = await page.evaluate(
            """(marcadorIso) => {
                const rows = [...document.querySelectorAll('tr')];
                const isoMin = marcadorIso;  // 'YYYY-MM-DDTHH:MM'
                const candidatos = [];
                for (let i = 0; i < rows.length; i++) {
                    const r = rows[i];
                    const tds = [...r.querySelectorAll('td')];
                    if (tds.length < 4) continue;
                    const txt = tds.map(t => (t.textContent || '').trim());
                    const all = txt.join(' | ');
                    if (!/CT-?e|Modelo\\s*17/i.test(all)) continue;
                    if (!/Gerado/i.test(all)) continue;
                    // procura timestamp DD/MM/AAAA, HH:MM:SS no row
                    const dts = [];
                    for (const t of txt) {
                        const m = t.match(/(\\d{2})\\/(\\d{2})\\/(\\d{4}),?\\s+(\\d{2}):(\\d{2}):?(\\d{2})?/);
                        if (m) {
                            const iso = `${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}`;
                            dts.push(iso);
                        }
                    }
                    if (!dts.length) continue;
                    // usa o maior (processado em)
                    const maxIso = dts.sort().pop();
                    if (maxIso >= isoMin) {
                        candidatos.push({i, maxIso, all});
                    }
                }
                if (!candidatos.length) return -1;
                // pega o mais recente
                candidatos.sort((a, b) => b.maxIso.localeCompare(a.maxIso));
                return candidatos[0].i;
            }""",
            marcador_iso,
        )
        if linha_idx < 0:
            elapsed = int(time.monotonic() - inicio)
            log(f"    Meus Relatórios: ainda não tem CT-e gerado pra essa busca ({elapsed}s, máx {max_seconds}s)")
            await asyncio.sleep(15)
            continue

        log(f"    Meus Relatórios: relatório CT-e encontrado na linha {linha_idx}")

        # Captura URL do S3 via route interception ao clicar "Baixar PDF"
        s3_url_holder: dict = {}
        ev = asyncio.Event()

        async def _route_s3(route, request=None):
            u = route.request.url
            if ("s3.amazonaws.com" in u or "s3.us-east" in u or "gw-saas-relatorios" in u) and not ev.is_set():
                s3_url_holder["url"] = u
                ev.set()
            try:
                await route.continue_()
            except Exception:
                pass

        await context.route("**/*", _route_s3)
        try:
            # Clica no link "Baixar PDF" da linha encontrada
            clicked = await page.evaluate(
                """(idx) => {
                    const r = document.querySelectorAll('tr')[idx];
                    if (!r) return false;
                    const links = [...r.querySelectorAll('a, button')];
                    const link = links.find(l => /baixar/i.test(l.textContent || ''));
                    if (!link) return false;
                    link.click();
                    return true;
                }""",
                linha_idx,
            )
            if not clicked:
                log("    Meus Relatórios: link 'Baixar PDF' não achado na linha")
                return None
            try:
                await asyncio.wait_for(ev.wait(), timeout=30)
            except asyncio.TimeoutError:
                log("    Meus Relatórios: timeout aguardando S3 URL")
                return None
        finally:
            await context.unroute("**/*", _route_s3)

        url_pdf = s3_url_holder.get("url")
        if not url_pdf:
            return None

        try:
            async with _httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                resp = await client.get(url_pdf)
                body = resp.content
            if body and b"%PDF" in body[:10]:
                log(f"    ✅ PDF via Meus Relatórios: {len(body):,} bytes")
                return body
        except Exception as e:
            log(f"    Erro fetch S3: {e}")
        return None


# ─── S3 CAPTURE ──────────────────────────────────────────────────────────────


# ─── FATURAS PDF ─────────────────────────────────────────────────────────────
# Fluxo: /consultafatura?acao=iniciar
#   → filtro Data de Emissão = hoje, filial
#   → selecionar checkboxes das faturas da factory
#   → "Modelo de impressão em PDF" = Modelo 10
#   → clicar ícone PDF (elemento com onclick*="popFatura")
#   → popup abre → GET direto na URL → PDF bytes

async def baixar_faturas_pdf(
    faturas_por_factory: dict[str, list[dict]],
    status: dict,
):
    log = lambda msg: status["logs"].append(msg)
    resumo_docs = status.setdefault("resumo_documentos", {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs(headless=False))
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        try:
            await _login_gw(page, user_id=status.get("usuario_id"))

            for sistema, faturas in faturas_por_factory.items():
                if not faturas:
                    continue

                nome_factory = _nome_factory(sistema)
                numeros_raw = {f["numero"].split("/")[0].strip() for f in faturas}
                numeros_norm = {_normalizar(n) for n in numeros_raw}
                filial_id = _FILIAL_ID.get(sistema, "1")
                rd = resumo_docs.setdefault(sistema, {"nome": nome_factory, "fatura_pdf": None, "ctes": []})

                log(f"📄 Fatura PDF — {nome_factory} ({len(numeros_raw)} fatura(s))...")
                log(f"  🔎 Faturas: {sorted(numeros_raw)}")

                try:
                    # Submete o formulário para garantir que o GW atualize a sessão corretamente.
                    # Navegação direta para acao=consultar é ignorada — GW usa estado da sessão.
                    hoje = _hoje_gw()
                    filial_id = _FILIAL_ID.get(sistema, "1")

                    await page.goto(
                        f"{BASE_GW}/consultafatura?acao=iniciar",
                        wait_until="domcontentloaded", timeout=30000
                    )
                    await page.locator('select[name="campoDeConsulta"]').wait_for(state="visible", timeout=15000)

                    titulo = await page.title()
                    log(f"  Pagina: {titulo} | filialId={filial_id}")
                    if "500" in titulo or "status 500" in titulo.lower():
                        raise Exception("GW retornou 500 na tela de faturas.")

                    await page.select_option('select[name="campoDeConsulta"]', value="emissao_fatura")
                    await page.fill('input[name="dtemissao1"]', hoje)
                    await page.fill('input[name="dtemissao2"]', hoje)
                    # Usa value numérico (não label) para evitar erro de texto exato
                    await page.select_option('select[name="filialId"]', value=filial_id)
                    await page.select_option('select[name="finalizada"]', label="Todas")
                    await page.select_option('select[name="limiteResultados"]', value="200")

                    # Espera explicitamente a RESPOSTA HTTP do Pesquisar.
                    # Bug antigo: `wait_for_url(acao=consultar)` retornava
                    # imediatamente em iterações subsequentes, porque a URL
                    # já continha `acao=consultar` da busca anterior — o código
                    # então lia a página velha (da filial errada).
                    try:
                        async with page.expect_response(
                            lambda r: "consultafatura" in r.url and "acao=consultar" in r.url,
                            timeout=30000
                        ):
                            await page.click('input[value="Pesquisar"]')
                        log(f"  URL ok: {page.url[page.url.find('acao='):][:150]}")
                    except Exception as e_url:
                        log(f"  AVISO expect_response timeout: {e_url}")
                        log(f"  URL atual: {page.url}")
                    # Aguarda os checkboxes ck* aparecerem = resultados renderizados
                    try:
                        await page.wait_for_selector('input[id^="ck"]', state="visible", timeout=15000)
                    except Exception:
                        pass

                    await page.wait_for_timeout(300)

                    # Faturas retornadas pelo GW
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

                    # ── Seleciona checkboxes via Playwright nativo ────────────────
                    # PASSO 1: leitura — coleta ids e números (sem tocar no DOM)
                    # PASSO 2: ação   — usa page.locator() p/ referência fresca
                    # (ElementHandle fica stale se o GW atualiza o DOM no check)

                    all_cbs = await page.query_selector_all('input[id^="ck"]')
                    log(f"  Checkboxes encontrados: {len(all_cbs)}")
                    log(f"  Buscando: {sorted(numeros_norm)}")

                    # Passo 1: mapeia cb_id → num (leitura, sem check)
                    ids_marcar: list[tuple[str, str]] = []   # (cb_id, num)
                    ids_desmarcar: list[str] = []

                    for cb in all_cbs:
                        cb_id = await cb.get_attribute("id") or ""
                        if not re.match(r'^ck\d+$', cb_id):
                            continue

                        tr_text = await cb.evaluate("""el => {
                            let e = el.parentElement;
                            for (let i = 0; i < 8; i++) {
                                if (!e) return '';
                                if (e.tagName === 'TR') return e.innerText || '';
                                e = e.parentElement;
                            }
                            return '';
                        }""")

                        m = re.search(r'(\d{5,6})/\d{4}', tr_text)
                        if not m:
                            ids_desmarcar.append(cb_id)
                            continue

                        num = m.group(1)
                        log(f"  #{cb_id} → fatura {num} | norm={_normalizar(num)}")
                        if _normalizar(num) in numeros_norm or num in numeros_raw:
                            ids_marcar.append((cb_id, num))
                        else:
                            ids_desmarcar.append(cb_id)

                    # Passo 2: desmarca os que não pertencem a esta factory
                    for cb_id in ids_desmarcar:
                        try:
                            loc = page.locator(f'#{cb_id}')
                            if await loc.is_checked():
                                await loc.uncheck()
                        except Exception:
                            pass

                    # Passo 2: marca os que pertencem a esta factory (referência fresca)
                    marcadas = 0
                    nums_marcados: list[str] = []
                    for cb_id, num in ids_marcar:
                        log(f"  → Marcando fatura {num} (#{cb_id})")
                        try:
                            await page.locator(f'#{cb_id}').check()
                            marcadas += 1
                            nums_marcados.append(num)
                        except Exception as e_cb:
                            log(f"  ⚠️ Erro ao marcar {num}: {e_cb}")

                    # Faturas que o usuário pediu mas o GW não retornou na busca de hoje
                    nums_marcados_norm = {_normalizar(n) for n in nums_marcados}
                    faltando = sorted([
                        n for n in numeros_raw
                        if _normalizar(n) not in nums_marcados_norm
                    ])

                    if marcadas == 0:
                        log(f"  ⚠️ Nenhuma fatura marcada. Esperado: {sorted(numeros_norm)} | Página: {na_pagina}")
                        rd["fatura_pdf"] = {
                            "ok": False,
                            "motivo": "fatura não encontrada no GW (data de hoje)",
                            "faturas_faltando": sorted(numeros_raw),
                        }
                        continue

                    if faltando:
                        log(f"  ⚠️ {len(faltando)} fatura(s) faltando no GW: {faltando}")
                    log(f"  ✓ {marcadas} fatura(s) marcada(s)")

                    # Seleciona Modelo 10 — busca qualquer select que tenha opção "Modelo 10"
                    modelo_ok = await page.evaluate("""() => {
                        // Tenta primeiro pelo ID histórico
                        let s = document.getElementById('cbmodelo');
                        // Se não achar, busca qualquer select com opção Modelo 10
                        if (!s) {
                            s = [...document.querySelectorAll('select')]
                                .find(el => [...el.options].some(o => /Modelo\\s*10/i.test(o.text)));
                        }
                        if (!s) return 'select Modelo nao encontrado';
                        const opt = [...s.options].find(o => /Modelo\\s*10/i.test(o.text));
                        if (!opt) return 'Modelo 10 nao encontrado nas opcoes';
                        s.value = opt.value;
                        s.dispatchEvent(new Event('change', {bubbles: true}));
                        return 'OK: ' + opt.text.trim();
                    }""")
                    log(f"  Modelo 10: {modelo_ok}")

                    # ── Captura PDF da fatura ─────────────────────────────────────────
                    # context.route() instalado ANTES do clique elimina race condition:
                    # o PDF é interceptado mesmo que o Chrome o carregue instantaneamente.

                    # Botão imprimir PDF confirmado pelo inspetor: img#imprimirPDF.imagelink
                    _click_sel_fat = None
                    for _sel in [
                        'img#imprimirPDF',
                        'img.imagelink[id*="imprimir"]',
                        '[onclick*="popFatura"]',
                        'input[type="image"][src*="pdf"]',
                        'input[type="image"][src*="PDF"]',
                        'img[src*="pdf"]',
                        'input[type="image"]',
                    ]:
                        try:
                            if await page.locator(_sel).first.is_visible(timeout=600):
                                _click_sel_fat = _sel
                                break
                        except Exception:
                            continue

                    pdf_bytes = None
                    _pdf_fat_holder: dict = {"bytes": None}

                    async def _ctx_rota_fat(route, request):
                        try:
                            resp = await route.fetch()
                            body = await resp.body()
                            if body and b"%PDF" in body[:10]:
                                _pdf_fat_holder["bytes"] = body
                                log(f"  🎯 Fatura PDF interceptado: {len(body):,} bytes")
                            await route.fulfill(response=resp)
                        except Exception:
                            try:
                                await route.continue_()
                            except Exception:
                                pass

                    # Instala route no contexto antes do clique — cobre popup desde o primeiro request
                    await context.route("**/*", _ctx_rota_fat)
                    try:
                        # Hook NÃO-BLOQUEANTE: captura a URL E chama window.open original.
                        # Assim o popup abre normalmente (quando possível) e temos fallback.
                        await page.evaluate("""() => {
                            if (window._openHookInstalled) {
                                window._capturedPopupUrls = [];
                                return;
                            }
                            window._capturedPopupUrls = [];
                            const _orig = window.open.bind(window);
                            window.open = function(u, ...rest) {
                                if (u) window._capturedPopupUrls.push(u);
                                return _orig(u, ...rest);
                            };
                            window._openHookInstalled = true;
                        }""")

                        # Estratégia 1: expect_page pro caso do popup funcionar (fluxo original)
                        popup_fat = None
                        popup_url_fat = ""
                        try:
                            async with context.expect_page(timeout=8000) as _popup_fat_info:
                                if _click_sel_fat:
                                    await page.locator(_click_sel_fat).first.click()
                                    log(f"  Clicou: {_click_sel_fat}")
                                else:
                                    await page.evaluate(
                                        "() => { if (typeof popFatura==='function') popFatura('1'); }"
                                    )
                                    log("  Clique: popFatura JS (fallback)")
                            popup_fat = await _popup_fat_info.value
                            try:
                                await popup_fat.wait_for_url(
                                    lambda u: u not in ("about:blank", ""),
                                    timeout=20000,
                                )
                            except Exception:
                                pass
                            popup_url_fat = popup_fat.url
                            log(f"  Popup URL: {popup_url_fat[:80]}")
                        except Exception:
                            # Popup não abriu (headless). Usa a URL capturada via window.open hook.
                            log("  Popup não abriu; tentando URL capturada de window.open")
                            urls = await page.evaluate("() => window._capturedPopupUrls || []")
                            if urls:
                                popup_url_fat = urls[-1]
                                log(f"  URL via window.open: {popup_url_fat[:80]}")

                        # Aguarda route interceptar o PDF (se popup abriu e fez request)
                        # Poll fino — sai imediatamente quando o PDF chega
                        if popup_fat:
                            for _t in range(75):  # 75 × 0.4s = 30s
                                await asyncio.sleep(0.4)
                                if _pdf_fat_holder.get("bytes"):
                                    break
                        pdf_bytes = _pdf_fat_holder.get("bytes")

                        # Fallback: navega a URL em uma ABA nova do contexto.
                        # Em headless o popup não abre, mas uma page criada manualmente
                        # faz a mesma navegação, disparando route handler e redirect p/ S3.
                        if not pdf_bytes and popup_url_fat and "about:blank" not in popup_url_fat:
                            abs_url = popup_url_fat
                            if abs_url.startswith("/") or abs_url.startswith("./"):
                                abs_url = abs_url.lstrip("./")
                                if not abs_url.startswith("/"):
                                    abs_url = "/" + abs_url
                                abs_url = f"{BASE_GW}{abs_url}"
                            elif not abs_url.startswith("http"):
                                abs_url = f"{BASE_GW}/{abs_url}"

                            # Estratégia 2: abre uma aba nova e navega — equivalente ao popup
                            try:
                                aba_pdf = await context.new_page()
                                try:
                                    await aba_pdf.goto(abs_url, wait_until="load", timeout=30000)
                                except Exception:
                                    pass
                                for _t in range(75):  # 75 × 0.4s = 30s
                                    await asyncio.sleep(0.4)
                                    if _pdf_fat_holder.get("bytes"):
                                        break
                                pdf_bytes = _pdf_fat_holder.get("bytes")
                                if pdf_bytes:
                                    log(f"  ✅ PDF via nova aba: {len(pdf_bytes):,} bytes")
                                await aba_pdf.close()
                            except Exception as e:
                                log(f"  Erro aba nova: {e}")

                            # Estratégia 3: fetch direto (último recurso)
                            if not pdf_bytes:
                                try:
                                    resp_fat = await context.request.get(abs_url)
                                    body_fat = await resp_fat.body()
                                    log(f"  context.request status={resp_fat.status}, size={len(body_fat)}")
                                    if body_fat and b"%PDF" in body_fat[:10]:
                                        pdf_bytes = body_fat
                                        log(f"  ✅ PDF via context.request: {len(pdf_bytes):,} bytes")
                                except Exception as e:
                                    log(f"  context.request: {e}")

                        if popup_fat:
                            try:
                                await popup_fat.close()
                            except Exception:
                                pass

                    except Exception as e:
                        log(f"  Erro capturando fatura PDF: {e}")
                    finally:
                        await context.unroute("**/*", _ctx_rota_fat)

                    if not pdf_bytes or b"%PDF" not in pdf_bytes[:10]:
                        log(f"  ⚠️ PDF de fatura não capturado — {nome_factory}")
                        rd["fatura_pdf"] = {
                            "ok": False,
                            "motivo": "PDF não capturado",
                            "faturas_faltando": sorted(numeros_raw),
                        }
                        continue

                    nome_arquivo = f"Fatura - {nome_factory} - {_hoje_fmt()}.pdf"
                    status.setdefault("arquivos", {})[nome_arquivo] = pdf_bytes
                    pasta = status.get("pasta_destino", "")
                    if pasta:
                        Path(pasta).mkdir(parents=True, exist_ok=True)
                        (Path(pasta) / nome_arquivo).write_bytes(pdf_bytes)
                        log(f"  ✅ Salvo em disco: {pasta}\\{nome_arquivo}")
                    else:
                        log(f"  ✅ Salvo: {nome_arquivo} ({len(pdf_bytes):,} bytes)")
                    rd["fatura_pdf"] = {
                        "ok": True,
                        "arquivo": nome_arquivo,
                        "qtd": marcadas,
                        "faturas_marcadas": sorted(nums_marcados),
                        "faturas_faltando": faltando,
                    }

                except Exception as e:
                    log(f"  ❌ Erro fatura PDF {nome_factory}: {e}")
                    rd["fatura_pdf"] = {
                        "ok": False,
                        "motivo": str(e)[:120],
                        "faturas_faltando": sorted(numeros_raw),
                    }

        except Exception as e:
            log(f"  ❌ Erro geral faturas PDF: {e}")
            log(traceback.format_exc()[-600:])
        finally:
            await browser.close()


# ─── CTes PDF ────────────────────────────────────────────────────────────────
# Fluxo correto (confirmado pelo usuário):
#   Lançamentos → Conhecimentos → /consultaconhecimento?acao=iniciar
#   → 1° dropdown = "Número Fatura", digita número, ano, filial, status "Confirmado", 1000/pág
#   → Pesquisar → marcar todos (#ckTodos) → clicar #img_imprimir
#   → popup com PDF abre → capturar e salvar

async def baixar_ctes_pdf(
    faturas_por_factory: dict[str, list[dict]],
    status: dict,
):
    import httpx
    log = lambda msg: status["logs"].append(msg)
    resumo_docs = status.setdefault("resumo_documentos", {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs(headless=False))
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        try:
            await _login_gw(page, user_id=status.get("usuario_id"))

            for sistema, faturas in faturas_por_factory.items():
                if not faturas:
                    continue

                nome_factory = _nome_factory(sistema)
                pdfs_desta_factory: list[tuple] = []
                rd = resumo_docs.setdefault(sistema, {"nome": nome_factory, "boleto": None, "ctes": []})
                ctes_info: list[dict] = []

                log(f"📋 CTes — {nome_factory} ({len(faturas)} fatura(s))...")

                for fatura in faturas:
                    numero = fatura["numero"]
                    # GW exige 6 dígitos com zeros: "005148"
                    numero_busca = numero.split("/")[0].strip().zfill(6)
                    emissao = fatura.get("emissao", "")
                    ano_busca = emissao.split("/")[-1] if emissao and "/" in emissao else _ano_atual()
                    filial_label = "MATRIZ" if "matriz" in sistema else "Filial SP"

                    log(f"  🔍 Fatura {numero} → '{numero_busca}' / '{ano_busca}' / '{filial_label}'")

                    try:
                        # 1. Navega para CT-e — load é mais estável que networkidle no GW
                        await page.goto(
                            f"{BASE_GW}/CTeControlador?acao=listar&&tipoTransporte=false",
                            wait_until="load",
                            timeout=60000,
                        )
                        try:
                            await page.locator("#pesquisar").wait_for(state="visible", timeout=30000)
                        except Exception:
                            url_atual = page.url
                            try:
                                txt = (await page.inner_text("body", timeout=2000)).strip()[:300]
                            except Exception:
                                txt = "(sem texto)"
                            shot = await _salvar_screenshot_debug(page, f"cte-{numero}-tela-nao-carregou")
                            raise Exception(
                                f"Tela do CTeControlador não carregou (botão #pesquisar não apareceu). "
                                f"Usuário GW pode não ter permissão ao módulo CT-e. "
                                f"URL: {url_atual} | trecho: '{txt}'"
                                + (f" | screenshot: {shot}" if shot else "")
                            )

                        # 2. Filtro = "Número Fatura"
                        await page.locator("#campo_consulta").select_option(label="Número Fatura")

                        # 3. Aguarda campos dinâmicos (aparecem só após passo 2)
                        await page.locator("#valor_consulta").wait_for(state="visible", timeout=10000)
                        await page.locator("#valor_consulta2").wait_for(state="visible", timeout=10000)

                        # 4. Operador = "Igual ao número" (ope=4)
                        await page.evaluate("""() => {
                            const sels = [...document.querySelectorAll('select')];
                            const opSel = sels.find(s =>
                                [...s.options].some(o => /palavra|frase/i.test(o.text))
                            );
                            if (!opSel) return;
                            const opt4 = [...opSel.options].find(o => o.value === '4');
                            if (opt4) {
                                opSel.value = '4';
                                opSel.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                        }""")

                        # 5. Preenche número da fatura e ano
                        await page.locator("#valor_consulta").fill(numero_busca)
                        await page.locator("#valor_consulta2").fill(ano_busca)

                        # 6. Demais filtros
                        await page.locator("#statusCte").select_option(label="Confirmado")
                        await page.locator("#tipoTransporte").select_option(label="Todos")
                        # #limite: busca por texto parcial (label pode ser "1000 resultados")
                        await page.evaluate("""() => {
                            const s = document.querySelector('#limite');
                            if (!s) return;
                            const opt = [...s.options].find(o => o.text.includes('1000'));
                            if (opt) { s.value = opt.value; s.dispatchEvent(new Event('change', {bubbles:true})); }
                        }""")
                        await page.locator("#filial").select_option(label=filial_label)

                        # 6b. LIMPA filtros de data — busca é por número de FATURA, não por
                        # data de emissão do CT-e (CT-es da fatura podem ter sido emitidos
                        # em outros dias). O GW pré-preenche com hoje, então tem que zerar.
                        await page.evaluate("""() => {
                            const ids = ['dtemissao1','dtemissao2','dataEmissao1','dataEmissao2'];
                            for (const id of ids) {
                                const el = document.getElementById(id);
                                if (el) {
                                    el.value = '';
                                    el.dispatchEvent(new Event('change', {bubbles: true}));
                                }
                            }
                        }""")
                        log(f"    ✓ Filtros configurados (filial={filial_label}, sem filtro de data)")

                        # 7. Captura texto atual das ocorrências para detectar mudança
                        occ_antes = await page.evaluate(
                            "() => { if (!document.body) return ''; const m = document.body.innerText.match(/Total de Ocorr.ncias:\\s*\\d+/); return m ? m[0] : ''; }"
                        )

                        # 8. Pesquisa — chama consulta() diretamente, bypassa session_test.jsp
                        #    (tryRequestToServer verifica session_test.jsp que retorna vazio em
                        #     contexto automatizado e cancela silenciosamente a busca)
                        await page.evaluate("""() => {
                            if (typeof consulta === 'function') {
                                consulta(
                                    document.getElementById('campo_consulta').value,
                                    document.getElementById('operador_consulta') ?
                                        document.getElementById('operador_consulta').value : '4',
                                    document.getElementById('valor_consulta').value,
                                    document.getElementById('limite').value,
                                    'pesquisar',
                                    document.getElementById('ordenacao') ?
                                        document.getElementById('ordenacao').value : 'numero',
                                    document.getElementById('tipo_ordenacao') ?
                                        document.getElementById('tipo_ordenacao').value : 'ASC',
                                    document.getElementById('valor_consulta2').value
                                );
                            } else {
                                document.getElementById('pesquisar').click();
                            }
                        }""")

                        # 9. Espera responsiva: poll do DOM com log de progresso, hard cap 5min.
                        #    Faturas com muitos CT-es podem demorar 1-3 minutos no GW.
                        try:
                            achou = await _aguardar_busca_cte(
                                page, occ_antes, numero, log,
                                max_seconds=120,        # 2 min — sai antes em 0 resultados
                                poll_seconds=0.4,       # poll fino — saída imediata
                                log_every_seconds=15.0, # mostra progresso a cada 15s
                            )
                        except Exception as e:
                            diag = await _diagnosticar_busca_cte(page, numero, filial_label)
                            raise Exception(
                                f"Busca CT-e da fatura {numero} interrompida: {e}. {diag}"
                            )
                        if not achou:
                            diag = await _diagnosticar_busca_cte(page, numero, filial_label)
                            raise Exception(
                                f"Busca CT-e da fatura {numero} não retornou em 5 min. {diag}"
                            )

                        # 10. Verifica total de CT-es encontrados
                        page_text = await page.inner_text("body")
                        m_occ = re.search(r"(?:Total de Ocorr[êe]ncias|Ocorr[êe]ncias):\s*(\d+)", page_text)
                        total_ctes = int(m_occ.group(1)) if m_occ else -1
                        log(f"    Ocorrências: {total_ctes}")

                        if total_ctes == 0:
                            log(f"  ⚠️ Nenhum CT-e para fatura {numero}")
                            ctes_info.append({"numero": numero, "ok": False, "qtd": 0, "motivo": "sem resultados"})
                            continue

                        # 11. Seleciona todos com #ckTodos
                        await page.locator("#ckTodos").wait_for(state="visible", timeout=10000)
                        await page.locator("#ckTodos").check()
                        await page.wait_for_function(
                            "() => document.querySelectorAll('input[type=checkbox]:checked').length > 0",
                            timeout=10000,
                        )
                        log(f"    ✓ Todos os CT-es selecionados")

                        # 12. Clica #img_imprimir e captura o PDF via context.route()
                        # context.route() instalado ANTES do clique — sem race condition.
                        # O GW abre redireciona_relatorio.jsp (HTML de espera) e JS carrega
                        # o PDF real; route.fetch() intercepta o PDF antes do Chrome consumi-lo.

                        pdf_bytes = None
                        _pdf_cte_holder: dict = {"bytes": None}

                        async def _ctx_rota_cte(route, request):
                            try:
                                resp = await route.fetch()
                                body = await resp.body()
                                if body and b"%PDF" in body[:10]:
                                    _pdf_cte_holder["bytes"] = body
                                    log(f"    🎯 CTe PDF interceptado: {len(body):,} bytes — {request.url[:70]}")
                                await route.fulfill(response=resp)
                            except Exception:
                                try:
                                    await route.continue_()
                                except Exception:
                                    pass

                        # Marca o instante ANTES de clicar — usado pelo fallback Meus Relatórios
                        marcador_relatorio = datetime.now()

                        # Instala no CONTEXTO antes do clique
                        await context.route("**/*", _ctx_rota_cte)
                        try:
                            async with context.expect_page(timeout=15000) as _popup_cte_info:
                                await page.locator("#img_imprimir").click()
                                log(f"    Clicou #img_imprimir")

                            popup_cte = await _popup_cte_info.value

                            # Estratégia adaptativa:
                            # - Faturas pequenas (<= 50 CT-es): popup geralmente devolve o PDF direto.
                            #   Espera curta (60s); se não vier, cai nos fallbacks.
                            # - Faturas grandes (> 50 CT-es): GW manda pra fila assíncrona.
                            #   Pula popup e vai direto pro Meus Relatórios.
                            CTES_LIMITE_FILA = 50
                            if total_ctes > CTES_LIMITE_FILA:
                                log(f"    📦 {total_ctes} CT-es: pulando popup, indo direto pro Meus Relatórios")
                                # Pequena pausa pra garantir que o servidor recebeu o pedido
                                await asyncio.sleep(3)
                                pdf_bytes = None
                            else:
                                # Aguarda o JS do GW gerar e carregar o PDF.
                                # Poll fino (0.4s) — sai imediatamente quando o PDF chega.
                                # Cap = 60s (150 × 0.4s); log a cada 20s.
                                for _t in range(150):
                                    await asyncio.sleep(0.4)
                                    if _pdf_cte_holder.get("bytes"):
                                        break
                                    if _t > 0 and _t % 50 == 0:
                                        log(f"    ⏳ Aguardando PDF CT-e... ({int(_t * 0.4)}s, máx 60s)")
                                pdf_bytes = _pdf_cte_holder.get("bytes")

                            # Fallback: popup navegou para URL direta do PDF
                            if not pdf_bytes:
                                popup_url_cte = popup_cte.url
                                log(f"    Popup URL final: {popup_url_cte[:120]}{'...' if len(popup_url_cte)>120 else ''}")

                                # redireciona_relatorio.jsp tem ?url=... com a URL real do listar_cte.jsp
                                # Extrai e tenta a URL interna
                                fetch_urls: list[str] = []
                                if "redireciona_relatorio" in popup_url_cte:
                                    try:
                                        from urllib.parse import urlparse, parse_qs, urljoin, unquote
                                        qs = parse_qs(urlparse(popup_url_cte).query)
                                        url_interna = qs.get("url", [""])[0]
                                        if url_interna:
                                            url_interna = unquote(url_interna)
                                            if not url_interna.startswith("http"):
                                                url_interna = urljoin(BASE_GW + "/", url_interna.lstrip("./"))
                                            fetch_urls.append(url_interna)
                                            log(f"    redireciona_relatorio → {url_interna[:120]}{'...' if len(url_interna)>120 else ''}")
                                    except Exception as e:
                                        log(f"    Erro ao parsear redireciona_relatorio: {e}")

                                # Sempre tenta a URL final como segundo recurso
                                if popup_url_cte and "about:blank" not in popup_url_cte:
                                    fetch_urls.append(popup_url_cte)

                                for fb_url in fetch_urls:
                                    if pdf_bytes: break
                                    try:
                                        resp_fb = await context.request.get(fb_url)
                                        body_fb = await resp_fb.body()
                                        if body_fb and b"%PDF" in body_fb[:10]:
                                            pdf_bytes = body_fb
                                            log(f"    ✅ PDF via fallback ({fb_url[:60]}...): {len(pdf_bytes):,} bytes")
                                            break
                                    except Exception as e:
                                        log(f"    Fallback GET: {e}")

                            # Último fallback: busca src de embed/object/iframe com PDF no DOM
                            if not pdf_bytes:
                                try:
                                    pdf_src = await popup_cte.evaluate("""
                                        () => {
                                            for (const el of document.querySelectorAll('embed,object,iframe')) {
                                                const src = el.src || el.data || '';
                                                if (src.toLowerCase().includes('.pdf') ||
                                                    el.type === 'application/pdf') return src;
                                            }
                                            return '';
                                        }
                                    """)
                                    if pdf_src:
                                        log(f"    DOM PDF src: {pdf_src[:80]}")
                                        _rd = await context.request.get(pdf_src)
                                        _rb = await _rd.body()
                                        if _rb and b"%PDF" in _rb[:10]:
                                            pdf_bytes = _rb
                                            log(f"    ✅ PDF via DOM: {len(pdf_bytes):,} bytes")
                                except Exception as e:
                                    log(f"    DOM fallback: {e}")

                            try:
                                await popup_cte.close()
                            except Exception:
                                pass

                        except Exception as e:
                            log(f"    Popup nao abriu: {e}")
                        finally:
                            await context.unroute("**/*", _ctx_rota_cte)

                        # Fallback final: Meus Relatórios. Quando a fatura tem muitos
                        # CT-es, o GW às vezes processa de forma assíncrona — o popup
                        # não retorna o PDF, mas ele aparece em /RelatorioControlador
                        # como um relatório novo (Modelo 17, Status=Gerado).
                        if not pdf_bytes or b"%PDF" not in pdf_bytes[:10]:
                            log(f"    Tentando fallback Meus Relatórios...")
                            try:
                                pdf_bytes = await _baixar_cte_de_meus_relatorios(
                                    page, context, marcador_relatorio, log,
                                    max_seconds=180,  # 3min — relatórios já chegam em 1-2min
                                )
                            except Exception as e:
                                log(f"    Erro fallback Meus Relatórios: {e}")

                        if not pdf_bytes or b"%PDF" not in pdf_bytes[:10]:
                            log(f"  ⚠️ PDF CT-e não capturado — fatura {numero}")
                            ctes_info.append({"numero": numero, "ok": False, "qtd": total_ctes, "motivo": "PDF não capturado"})
                            continue

                        nome_arquivo = f"CTe - Fatura {numero}.pdf"
                        # Não adiciona individualmente a arquivos — será incluído no ZIP da factory
                        pdfs_desta_factory.append((nome_arquivo, pdf_bytes))

                        log(f"  ✅ CT-e(s) fatura {numero} — {len(pdf_bytes):,} bytes")
                        ctes_info.append({"numero": numero, "ok": True, "qtd": total_ctes})

                    except Exception as e:
                        log(f"  ❌ Fatura {numero}: {e}")
                        log(traceback.format_exc()[-400:])
                        ctes_info.append({"numero": numero, "ok": False, "qtd": 0, "motivo": str(e)[:120]})
                        continue

                rd["ctes"] = ctes_info

                if pdfs_desta_factory:
                    nome_zip = f"CTEs - {nome_factory} - {_hoje_fmt()}.zip"
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        for nome_pdf, dados_pdf in pdfs_desta_factory:
                            zf.writestr(nome_pdf, dados_pdf)
                    zip_bytes = buf.getvalue()
                    status.setdefault("arquivos", {})[nome_zip] = zip_bytes
                    log(f"  📦 ZIP: {nome_zip} ({len(pdfs_desta_factory)} CT-e(s))")

                    pasta = status.get("pasta_destino", "")
                    if pasta:
                        Path(pasta).mkdir(parents=True, exist_ok=True)
                        (Path(pasta) / nome_zip).write_bytes(zip_bytes)
                        log(f"  📁 ZIP salvo em disco: {pasta}\\{nome_zip}")

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

    log("📄 ETAPA 1: Baixando PDF das faturas (Modelo 10)...")
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
    total_faturas_ok = sum(1 for v in rd.values() if v.get("fatura_pdf") and v["fatura_pdf"].get("ok"))
    total_ctes_ok = sum(
        sum(1 for c in v.get("ctes", []) if c.get("ok")) for v in rd.values()
    )
    total_zips = sum(1 for v in rd.values() if v.get("zip") and v["zip"].get("ok"))
    total_arquivos = len(status.get("arquivos", {}))

    log("=" * 50)
    if total_arquivos:
        log(f"✅ {total_arquivos} arquivo(s) prontos para download")
        log(f"   📄 {total_faturas_ok} fatura(s) PDF")
        log(f"   📋 {total_ctes_ok} CTe(s) PDF agrupados por fatura")
        log(f"   📦 {total_zips} ZIP(s)")
    else:
        log("⚠️ Nenhum documento gerado — verifique os logs acima")
    log("=" * 50)
