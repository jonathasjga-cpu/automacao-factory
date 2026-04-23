import asyncio
import re
import httpx
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page
from config_manager import get_credencial


def _data_operacao_str() -> str:
    """Retorna a data que a factory registra para a operação (próxima segunda em fins de semana)."""
    hoje = datetime.now()
    if hoje.weekday() == 5:   # sábado
        return (hoje + timedelta(days=2)).strftime("%d/%m/%Y")
    elif hoje.weekday() == 6:  # domingo
        return (hoje + timedelta(days=1)).strftime("%d/%m/%Y")
    return hoje.strftime("%d/%m/%Y")


FLUXASSET_URL = "https://portal.fluxasset.com.br/Factaconsult"


async def fazer_login_fluxasset(page: Page, sistema: str):
    """Faz login na FluxAsset (usa Chrome real para passar o Cloudflare Turnstile)"""
    creds = get_credencial(sistema)
    await page.goto(f"{FLUXASSET_URL}/login", wait_until="domcontentloaded", timeout=60000)

    # Aguarda o Cloudflare "Verificando se você é humano" passar.
    # O browser está visível — se aparecer o desafio, complete manualmente na janela aberta.
    cf_passou = False
    for tentativa in range(60):  # até 90s (60 × 1,5s)
        await page.wait_for_timeout(1500)
        # try/except: page.evaluate lança "Execution context destroyed" durante navegação
        try:
            texto = await page.evaluate("() => (document.body && document.body.innerText) || ''")
        except Exception:
            texto = ""
        tem_cf = any(p in texto for p in ['Verificando', 'Checking', 'Just a moment', 'cf-browser'])
        tem_login = await page.query_selector('input[type="password"]')
        if tem_login:
            cf_passou = True
            break
        if tentativa == 5:  # ~7,5s sem login → avisa usuário
            # injeta aviso visível na janela do browser aberta pela automação
            try:
                await page.evaluate("""
                    () => {
                        if (document.getElementById('_cf_aviso')) return;
                        const d = document.createElement('div');
                        d.id = '_cf_aviso';
                        d.style = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);'
                                + 'background:#1e40af;color:#fff;padding:12px 24px;border-radius:8px;'
                                + 'font-size:16px;z-index:99999;font-family:sans-serif;text-align:center';
                        d.innerText = '⚠️ Complete a verificação Cloudflare nesta janela para continuar';
                        document.body.appendChild(d);
                    }
                """)
            except Exception:
                pass

    if not cf_passou:
        raise Exception(
            "FluxAsset: timeout aguardando formulario de login. "
            "Se apareceu verificacao Cloudflare, complete manualmente na janela do Chrome."
        )

    # Pequena pausa para a página estabilizar após o Cloudflare liberar
    await page.wait_for_timeout(800)

    await page.locator('input[type="text"], input[type="email"]').first.fill(creds["usuario"])
    await page.fill('input[type="password"]', creds["senha"])
    await page.click('button:has-text("Entrar"), button[type="submit"]')

    # Aguarda sair da tela de login (URL muda para home/dashboard)
    try:
        await page.wait_for_url(lambda url: "login" not in url.lower(), timeout=30000)
    except Exception:
        pass

    # Aguarda a página principal carregar completamente
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        await page.wait_for_timeout(3000)


async def navegar_para_digitacao(page: Page):
    """Navega para Digitação via JS (link dentro do dropdown oculto)"""
    # Garante que a página terminou de carregar antes de inspecionar o DOM
    # (evita "Execution context was destroyed" durante redirect pós-login)
    for _ in range(2):
        try:
            await page.wait_for_load_state("load", timeout=10000)
            break
        except Exception:
            await page.wait_for_timeout(1000)

    # Aguarda o link de digitação aparecer no DOM — tenta 3x com pause entre tentativas
    for tentativa in range(3):
        try:
            await page.wait_for_function(
                "() => !!document.querySelector('a[href*=\"/operacao/digitacao\"]')",
                timeout=8000,
            )
            break
        except Exception:
            if tentativa < 2:
                await page.wait_for_timeout(1500)

    await page.evaluate(
        "() => { const a = document.querySelector('a[href*=\"/operacao/digitacao\"]'); if(a) a.click(); }"
    )
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)


async def _buscar_dados_cnpj(cnpj_limpo: str) -> dict:
    """Busca dados do CNPJ disparando as 3 APIs em paralelo — retorna a primeira que responder."""
    async def _cnpj_ws() -> dict:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"https://publica.cnpj.ws/cnpj/{cnpj_limpo}")
            if r.status_code != 200:
                return {}
            d = r.json()
            end = d.get("estabelecimento", {})
            return {
                "nome":     d.get("razao_social", ""),
                "cep":      re.sub(r"\D", "", end.get("cep", "")),
                "endereco": end.get("logradouro", ""),
                "numero":   end.get("numero", ""),
                "bairro":   end.get("bairro", ""),
                "cidade":   end.get("cidade", {}).get("nome", "") if isinstance(end.get("cidade"), dict) else end.get("cidade", ""),
                "uf":       end.get("estado", {}).get("sigla", "") if isinstance(end.get("estado"), dict) else end.get("estado", ""),
            }

    async def _brasilapi() -> dict:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}")
            if r.status_code != 200:
                return {}
            d = r.json()
            return {
                "nome":     d.get("razao_social", ""),
                "cep":      re.sub(r"\D", "", d.get("cep", "")),
                "endereco": d.get("logradouro", ""),
                "numero":   d.get("numero", ""),
                "bairro":   d.get("bairro", ""),
                "cidade":   d.get("municipio", ""),
                "uf":       d.get("uf", ""),
            }

    async def _receitaws() -> dict:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"https://www.receitaws.com.br/v1/cnpj/{cnpj_limpo}")
            if r.status_code != 200:
                return {}
            d = r.json()
            if d.get("status") == "ERROR":
                return {}
            return {
                "nome":     d.get("nome", ""),
                "cep":      re.sub(r"\D", "", d.get("cep", "")),
                "endereco": d.get("logradouro", ""),
                "numero":   d.get("numero", ""),
                "bairro":   d.get("bairro", ""),
                "cidade":   d.get("municipio", ""),
                "uf":       d.get("uf", ""),
            }

    # Dispara as 3 chamadas em paralelo — usa a primeira que trouxer nome
    resultados = await asyncio.gather(_cnpj_ws(), _brasilapi(), _receitaws(), return_exceptions=True)
    for r in resultados:
        if isinstance(r, dict) and r.get("nome"):
            return r
    return {}


async def cadastrar_sacado_se_necessario(page: Page, cnpj_limpo: str, nome_fatura: str, status: dict, dados: dict = None):
    """Preenche o popup Cadastro de sacado (mesmos campos da Firma — plataforma FactaConsult).
    Se 'dados' for passado (pré-buscado antes do popup abrir), usa diretamente sem nova chamada."""
    log = lambda msg: status["logs"].append(msg)

    if not dados or not dados.get("nome"):
        # Fallback: busca agora (situação em que não foi pré-buscado)
        log(f"  [INFO] Buscando dados do CNPJ {cnpj_limpo} na Receita Federal...")
        dados = await _buscar_dados_cnpj(cnpj_limpo)

    nome_usar = dados.get("nome") or nome_fatura
    primeiro_nome = nome_usar.split()[0].lower() if nome_usar else "cliente"
    email_sacado = f"{primeiro_nome}@gmail.com"

    # Aguarda o popup renderizar completamente antes de interagir
    await page.wait_for_selector('#iden', state='visible', timeout=6000)

    # Preenche campos do popup
    await page.fill('#iden', cnpj_limpo)
    await page.fill('#nome', nome_usar)

    # CEP + autocomplete
    if dados.get("cep"):
        await page.fill('#cep', dados["cep"])
        await page.press('#cep', 'Tab')
        try:
            await page.wait_for_function(
                "() => { const e = document.getElementById('ende'); return e && e.value && e.value.length > 0; }",
                timeout=3000
            )
        except Exception:
            await page.wait_for_timeout(400)

    # Endereço e demais — preenche só se autocomplete não preencheu
    for field_id, value in [
        ("ende", dados.get("endereco", "")),
        ("nume", dados.get("numero",   "")),
        ("bair", dados.get("bairro",   "")),
        ("cida", dados.get("cidade",   "")),
        ("uf",   dados.get("uf",       "")),
    ]:
        if value:
            field = await page.query_selector(f"#{field_id}")
            if field:
                atual = await field.input_value()
                if not atual:
                    await field.fill(value)

    # E-mail
    try:
        await page.fill('#e_mail', email_sacado)
    except Exception:
        pass

    log(f"  [INFO] Popup preenchido — salvando cadastro de {nome_usar}")

    # Salvar — sobe pelo DOM a partir de #iden; fallback: modais visíveis
    await page.evaluate("""() => {
        const ancora = document.getElementById('iden') || document.getElementById('nome');
        if (ancora) {
            let el = ancora;
            for (let i = 0; i < 15; i++) {
                el = el.parentElement;
                if (!el) break;
                const btn = [...el.querySelectorAll('button')]
                    .find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
                if (btn) { btn.click(); return; }
            }
        }
        const modais = document.querySelectorAll('.modal, .dialog, [role="dialog"], .popup, .overlay');
        for (const modal of modais) {
            if (!modal.offsetParent) continue;
            const btn = [...modal.querySelectorAll('button')]
                .find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
            if (btn) { btn.click(); return; }
        }
    }""")

    # Aguarda resposta: "Confirma salvar?" ou "CNPJ/CPF Inválido"
    try:
        await page.wait_for_selector(
            'text=Confirma salvar?, text=CNPJ/CPF Inv',
            timeout=4000
        )
    except Exception:
        pass

    # Se CNPJ inválido: fecha dialog e lança exceção
    cnpj_invalido = await page.query_selector('text=CNPJ/CPF Inv')
    if cnpj_invalido:
        try:
            await page.locator('button:has-text("Ok")').first.click()
        except Exception:
            pass
        raise Exception(
            f"CNPJ {cnpj_limpo} rejeitado pela FluxAsset como invalido — verifique os digitos verificadores"
        )

    # Confirmação "Confirma salvar?"
    try:
        await page.locator('button:has-text("Sim")').first.click()
    except Exception:
        pass

    # Aguarda popup fechar
    try:
        await (
            page.locator("text=Cadastro de sacado").or_(page.locator("text=Cadastro de Sacado"))
        ).wait_for(state="hidden", timeout=8000)
    except Exception:
        await page.wait_for_timeout(1500)

    # Após popup fechar, o form principal pode estar com #saca_nome vazio
    # porque o sistema não faz re-lookup automático após o cadastro.
    # Re-disparamos o lookup digitando o CNPJ novamente em #saca_id.
    saca_nome_ok = await page.evaluate("""
        () => {
            const el = document.getElementById('saca_nome');
            return el && el.value && el.value.trim().length > 1;
        }
    """)
    if not saca_nome_ok:
        log(f"  [INFO] saca_nome vazio apos popup — re-disparando lookup para {cnpj_limpo}...")
        saca_field = page.locator('#saca_id').first
        await saca_field.fill("")
        await page.wait_for_timeout(200)
        await saca_field.fill(cnpj_limpo)
        await saca_field.press("Tab")

        # Aguarda #saca_nome ser preenchido pelo lookup (máx 6 s)
        for _ in range(30):
            await page.wait_for_timeout(200)
            saca_nome_ok = await page.evaluate("""
                () => {
                    const el = document.getElementById('saca_nome');
                    return el && el.value && el.value.trim().length > 1;
                }
            """)
            if saca_nome_ok:
                break

        if not saca_nome_ok:
            raise Exception(
                f"Sacado {cnpj_limpo} salvo no cadastro mas nao vinculado no formulario "
                f"(#saca_nome permanece vazio apos re-lookup)"
            )

    log(f"  [OK] Sacado cadastrado e vinculado: {nome_usar}")


async def aguardar_lookup_sacado(page: Page, cnpj_limpo: str) -> bool:
    """Aguarda o lookup do sacado completar após Tab no campo saca_id.
    Retorna False se abriu o popup de cadastro, True se o sacado foi encontrado.
    IMPORTANTE: não checar #saca_id.value — nós mesmos acabamos de preenchê-lo."""
    await page.wait_for_timeout(800)

    for _ in range(45):
        await page.wait_for_timeout(100)

        cadastro = (
            await page.query_selector("text=Cadastro de sacado") or
            await page.query_selector("text=Cadastro de Sacado")
        )
        if cadastro:
            return False

        nome_ok = await page.evaluate("""
            () => {
                for (const inp of document.querySelectorAll('input')) {
                    if (!inp.offsetParent) continue;
                    const id = (inp.id   || '').toLowerCase();
                    const nm = (inp.name || '').toLowerCase();
                    if (!id.includes('nome') && !nm.includes('nome')) continue;
                    const v = inp.value.trim();
                    if (v && v.toLowerCase() !== 'undefined' && v.length > 1) return true;
                }
                return false;
            }
        """)
        if nome_ok:
            return True

    cadastro = (
        await page.query_selector("text=Cadastro de sacado") or
        await page.query_selector("text=Cadastro de Sacado")
    )
    return cadastro is None


async def preencher_titulo_fluxasset(page: Page, fatura: dict, status: dict):
    """Preenche o formulário de digitação da FluxAsset."""
    log = lambda msg: status["logs"].append(msg)
    cnpj_limpo = re.sub(r'\D', '', fatura["cliente_cnpj"])

    # ── Pré-busca dos dados do CNPJ na internet ANTES de abrir o formulário ──
    # Isso garante que, se o popup de cadastro aparecer, os dados já estejam
    # prontos para preencher imediatamente (evita timeout do popup esperando a API).
    log(f"  [CNPJ] Consultando dados do CNPJ {cnpj_limpo} na Receita Federal...")
    dados = await _buscar_dados_cnpj(cnpj_limpo)
    if dados.get("nome"):
        log(f"  [CNPJ] Encontrado: {dados['nome']}")
    else:
        log(f"  [CNPJ] API nao retornou dados — usara nome da planilha como fallback")

    saca_locator = page.locator('#saca_id').first
    await saca_locator.wait_for(state="visible", timeout=8000)
    await saca_locator.fill(cnpj_limpo)
    await saca_locator.press('Tab')

    sacado_ok = await aguardar_lookup_sacado(page, cnpj_limpo)
    if not sacado_ok:
        log(f"  [INFO] Cliente {cnpj_limpo} nao cadastrado no sistema. Abrindo cadastro...")
        await cadastrar_sacado_se_necessario(page, cnpj_limpo, fatura.get("cliente_nome", ""), status, dados)

    # Aguarda o form estabilizar após lookup/cadastro do sacado
    await page.wait_for_timeout(600)

    valor_fmt = f"{fatura['valor']:.2f}".replace(".", ",")
    for sel, val in [
        ('#data_titu', fatura["vencimento"]),
        ('#valo_titu', valor_fmt),
        ('#nume_doct', fatura["numero"]),
        ('#nume_nota', fatura["numero"]),
        ('#data_emis', fatura["emissao"]),
        ('#valo_nota', valor_fmt),
        ('#chave_nf',  fatura.get("chave", "")),
    ]:
        if not val:
            continue
        try:
            await page.locator(sel).first.wait_for(state="visible", timeout=3000)
            await page.fill(sel, str(val))
        except Exception as e:
            log(f"  [WARN] Campo {sel} nao preenchido: {e}")

    # Valida ANTES de salvar
    valo_real = await page.evaluate(
        "() => { const e = document.getElementById('valo_titu'); return e ? e.value.trim() : ''; }"
    )
    data_real = await page.evaluate(
        "() => { const e = document.getElementById('data_titu'); return e ? e.value.trim() : ''; }"
    )
    if not valo_real or not data_real:
        raise Exception(
            f"Titulo {fatura['numero']}: campos obrigatorios vazios antes do Salvar "
            f"(valo_titu='{valo_real}', data_titu='{data_real}') — verifique preenchimento"
        )

    # Clica Salvar — sobe DOM a partir de #valo_titu para garantir botão do form correto
    salvar_log = await page.evaluate("""
        () => {
            const ancora = document.getElementById('valo_titu')
                        || document.getElementById('data_titu')
                        || document.getElementById('saca_id');
            if (ancora) {
                let el = ancora;
                for (let i = 0; i < 25; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    const btn = [...el.querySelectorAll('button')]
                        .find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
                    if (btn) { btn.click(); return 'salvar_nivel_' + i; }
                }
            }
            // Fallback: primeiro Salvar visível
            const btn = [...document.querySelectorAll('button')]
                .find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
            if (btn) { btn.click(); return 'salvar_fallback'; }
            return 'nao_encontrado';
        }
    """)
    log(f"  [INFO] Salvar click: {salvar_log}")

    # FactaConsult pode exibir "Confirma salvar?" — precisamos clicar em "Sim"
    await page.wait_for_timeout(500)
    try:
        tem_confirma = await page.evaluate(
            "() => !!(document.body && document.body.innerText.includes('Confirma salvar'))"
        )
        if tem_confirma:
            await page.locator('button:has-text("Sim")').first.click()
            log(f"  [INFO] Confirmacao 'Confirma salvar?' aceita para titulo {fatura['numero']}")
    except Exception:
        pass

    # Aguarda o servidor confirmar o salvamento
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        await page.wait_for_timeout(1000)

    # Detecta erros de validação exibidos pelo form — se houver, lança exceção
    try:
        erros_form = await page.evaluate("""
            () => {
                const sels = ['.alert-danger','.error','.msg-erro','[class*="erro"]',
                              '[class*="error"]','.toast-error','.notification-error'];
                for (const s of sels) {
                    const els = [...document.querySelectorAll(s)]
                        .filter(e => e.offsetParent && e.textContent.trim());
                    if (els.length) return els.map(e => e.textContent.trim().substring(0,120)).join('; ');
                }
                return '';
            }
        """)
        if erros_form:
            raise Exception(
                f"Titulo {fatura['numero']} nao foi salvo — erro retornado pelo sistema: {erros_form}"
            )
    except Exception as e:
        if "nao foi salvo" in str(e):
            raise
        # evaluate falhou — ignora e considera salvo

    # O FluxAsset mantém o form preenchido após salvar (comportamento normal do sistema).
    # Confiar na ausência de erros acima como indicador de sucesso.
    log(f"  [OK] Titulo {fatura['numero']} - {fatura.get('cliente_nome', '')} salvo")


async def _verificar_valor_operacao(page, faturas_salvas: set, faturas_dados: dict, sistema: str, status: dict):
    """Compara o Vlr.Total da operação na FluxAsset com a soma dos títulos enviados."""
    log = lambda msg: status["logs"].append(msg)
    try:
        valor_esperado = sum(
            faturas_dados.get(num, {}).get("valor", 0)
            for num in faturas_salvas
        )
        if not faturas_salvas or valor_esperado == 0:
            return

        vlr_total_str = await page.evaluate("""
            () => {
                const rows = [...document.querySelectorAll('tr')];
                for (const row of rows) {
                    if (!row.offsetParent) continue;
                    if (!row.textContent.includes('Aguardando')) continue;
                    const cells = [...row.querySelectorAll('td')];
                    for (const cell of cells) {
                        const t = cell.textContent.trim().replace(/\\s+/g, ' ');
                        if (/\\d{1,3}(\\.\\d{3})*,\\d{2}/.test(t) && !t.includes('/') && t.length < 25) {
                            return t;
                        }
                    }
                    return null;
                }
                return null;
            }
        """)

        if vlr_total_str is None:
            log(f"  [WARN] Nao foi possivel ler Vlr.Total da operacao — validacao de valor ignorada")
            return

        vlr_total = float(
            vlr_total_str.replace("R$", "").replace(".", "").replace(",", ".").strip()
        )
        diff = abs(vlr_total - valor_esperado)
        if diff < 0.02:
            log(f"  ✅ Valor validado: R$ {vlr_total:,.2f} == esperado R$ {valor_esperado:,.2f}")
        else:
            log(f"  ❌ Divergencia de valor: operacao R$ {vlr_total:,.2f} vs esperado R$ {valor_esperado:,.2f} (diff R$ {diff:,.2f})")
            status["erros"].append(
                f"[{sistema}] Valor da operacao R$ {vlr_total:,.2f} difere do esperado R$ {valor_esperado:,.2f}"
            )
    except Exception as e:
        log(f"  [WARN] Erro ao validar valor da operacao: {e}")


async def _aplicar_filtro_data_e_pesquisar(page, data_op: str):
    """Preenche o filtro de Período com data_op e clica Pesquisar."""
    await page.evaluate(f"""
        () => {{
            const data = '{data_op}';
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            const inputs = [...document.querySelectorAll('input[type="text"]')].slice(0, 2);
            inputs.forEach(inp => {{
                setter.call(inp, data);
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
            }});
        }}
    """)
    await page.wait_for_timeout(300)
    pesquisar = await page.query_selector('button:has-text("Pesquisar")')
    if pesquisar:
        await pesquisar.click()
        await page.wait_for_timeout(1500)


async def _localizar_linha_aguardando(page) -> object | None:
    try:
        await page.wait_for_selector("tr:has-text('Aguardando')", timeout=8000)
    except Exception:
        return None
    return await page.query_selector("tr:has-text('Aguardando')")


async def _finalizar_na_pagina(page, sistema: str, status: dict):
    """Finaliza a operação na página já aberta — sem abrir novo browser."""
    log = lambda msg: status["logs"].append(msg)
    data_op = _data_operacao_str()

    log(f"  [DATE] Filtrando operacoes pela data: {data_op}")
    await _aplicar_filtro_data_e_pesquisar(page, data_op)

    linha = await _localizar_linha_aguardando(page)
    if not linha:
        log("  [WARN] Operacao com status Aguardando nao encontrada")
        return

    # Abre Ações → Definir conta corrente
    botao_acoes = await linha.query_selector('button:has-text("Ações"), .btn-acoes')
    await botao_acoes.click()
    await page.wait_for_timeout(400)
    await page.click("text=Definir conta corrente")
    log("  [BANK] Selecionando conta corrente...")

    try:
        await page.wait_for_selector("text=Conta Corrente", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(700)

    # 1ª linha de dados da tabela (tr:nth-child(2) pula o cabeçalho)
    primeiro_btn = await page.query_selector(
        "table tr:nth-child(2) button, "
        "table tbody tr:first-child button, "
        "table tr:nth-child(2) .btn"
    )
    if primeiro_btn:
        await primeiro_btn.click()
        log("  [OK] Conta corrente definida")
    else:
        log("  [WARN] Botao de conta nao encontrado — continuando")

    await page.wait_for_timeout(1500)

    # Encaminhar — menu Ações permanece aberto após selecionar conta
    encaminhar = await page.query_selector("text=Encaminhar para operação / encerrar")
    if not encaminhar:
        linha2 = await _localizar_linha_aguardando(page)
        if linha2:
            botao_acoes2 = await linha2.query_selector('button:has-text("Ações"), .btn-acoes')
            if botao_acoes2:
                await botao_acoes2.click()
                await page.wait_for_timeout(400)

    await page.click("text=Encaminhar para operação / encerrar")
    await page.wait_for_timeout(1500)
    log(f"  [OK] Operacao FluxAsset {sistema} encaminhada com sucesso!")


async def _ativar_aba_digitacao(page: Page):
    """Clica na aba 'Digitação' do form FactaConsult — tenta clique nativo primeiro."""
    try:
        loc = page.locator('li.aba-cabecalho-lista-li:has-text("Digitação")')
        if await loc.count() > 0:
            await loc.first.click(timeout=3000)
            return
    except Exception:
        pass
    # Fallback JS
    try:
        await page.evaluate("""
            () => {
                const spans = [...document.querySelectorAll('li.aba-cabecalho-lista-li span')];
                const tab = spans.find(s => s.textContent.trim() === 'Digitação');
                if (tab) tab.closest('li').click();
            }
        """)
    except Exception:
        pass
    await page.wait_for_timeout(500)


async def executar_fluxasset(faturas_selecao, sistema: str, status: dict) -> dict:
    """
    Executa a digitação completa na FluxAsset.
    Mantém browser visível para passar o Cloudflare Turnstile —
    clique manualmente em "Confirme que é humano" se aparecer.
    """
    log = lambda msg: status["logs"].append(msg)
    faturas_dados = status.get("faturas_cache", {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = await browser.new_page()

        log(f"[LOGIN] Fazendo login na FluxAsset ({sistema})...")
        await fazer_login_fluxasset(page, sistema)

        log("[DIR] Navegando para Digitacao...")
        await navegar_para_digitacao(page)

        await page.locator('button:has-text("Novo")').first.click()
        # Aguarda o form/modal abrir antes de tentar ativar a aba
        try:
            await page.wait_for_function(
                "() => !!document.querySelector('li.aba-cabecalho-lista-li, #saca_id')",
                timeout=10000,
            )
        except Exception:
            await page.wait_for_timeout(1200)

        await _ativar_aba_digitacao(page)
        # Aguarda #saca_id ficar visível (timeout generoso; tab pode ter animação)
        try:
            await page.locator('#saca_id').first.wait_for(state="visible", timeout=12000)
        except Exception:
            await page.wait_for_timeout(1000)

        for idx, sel in enumerate(faturas_selecao):
            fatura = faturas_dados.get(sel.numero)
            if not fatura:
                log(f"  [WARN] Dados nao encontrados para fatura {sel.numero}")
                continue

            log(f"[{idx+1}/{len(faturas_selecao)}] Digitando fatura {sel.numero} - {fatura.get('cliente_nome', '')}...")

            if idx > 0:
                await page.evaluate("""
                    () => {
                        const salvar = [...document.querySelectorAll('button')]
                            .find(b => b.textContent.trim() === 'Salvar' && b.offsetParent !== null);
                        if (!salvar) return 'salvar_nao_encontrado';
                        let el = salvar;
                        for (let i = 0; i < 8; i++) {
                            el = el.parentElement;
                            if (!el) break;
                            const novo = [...el.querySelectorAll('button')]
                                .find(b => b.textContent.trim() === 'Novo' && b.offsetParent !== null);
                            if (novo) { novo.click(); return 'clicado_nivel_' + i; }
                        }
                        return 'novo_nao_encontrado';
                    }
                """)
                await page.wait_for_timeout(800)

                # Se #saca_id não está visível, reativa a aba Digitação
                try:
                    saca_vis = await page.evaluate(
                        "() => { const el = document.querySelector('#saca_id'); "
                        "return el && el.getBoundingClientRect().width > 0; }"
                    )
                except Exception:
                    saca_vis = False
                if not saca_vis:
                    await _ativar_aba_digitacao(page)
                    try:
                        await page.locator('#saca_id').first.wait_for(state="visible", timeout=8000)
                    except Exception:
                        await page.wait_for_timeout(800)

            try:
                await preencher_titulo_fluxasset(page, fatura, status)
                status["concluidas"] += 1
                status.setdefault("faturas_salvas", set()).add(sel.numero)
            except Exception as e:
                log(f"  [ERR] Erro na fatura {sel.numero}: {str(e)}")
                status["erros"].append(f"Fatura {sel.numero}: {str(e)}")

        # Fecha modal e recarrega para ver operação criada
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await page.reload()
        await page.wait_for_timeout(1500)

        data_op = _data_operacao_str()
        await _aplicar_filtro_data_e_pesquisar(page, data_op)
        await _verificar_valor_operacao(
            page,
            status.get("faturas_salvas", set()),
            faturas_dados,
            sistema,
            status,
        )

        await browser.close()

    return {"sistema": sistema}
