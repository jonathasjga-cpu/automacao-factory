# -*- coding: utf-8 -*-
"""Digitacao de titulos na GC Recursos — portal SIFAC (novo).

A GC trocou de plataforma: saiu do FactaConsult (gcrecursos.dyndns.org:9000)
e foi pro SIFAC (app.sifacweb.com.br/gcsecuritizadora). Sistema diferente,
seletores diferentes — este modulo substitui o gc_digitacao.py, que fica no
repo apenas como referencia do portal antigo.

CALIBRADO por sondagem ao vivo (scratchpad/sonda3.py e sonda4.py). Medido:

  Login          #userName (email) + #password + botao "Entrar"
  Rota direta    {BASE}/Operacao/Incluir   (nao precisa navegar pelo menu)
  Abrir form     botao "Incluir Titulo"
  Campos do modal:
    #tipo_recebivel      SELECT  DM | CH | DS | CM      -> DM (duplicata)
    #nuDocumento         maxlen 10   (preserva zeros a esquerda: "011718")
    #vl_nominal          "1234,56" -> exibe "1.234,56"  (mascara de milhar)
    dt_emissao           maxlen 10   dd/mm/aaaa  (SEM id — usar name)
    dt_vencimento        maxlen 10   dd/mm/aaaa  (SEM id — usar name)
    #tipo_sacado         SELECT  PJ | PF
    #cnpj                maxlen 18   aceita so digitos, formata sozinho
    #cd_chavenfe         maxlen 44
    #fl_terceiro         checkbox "Duplicata de Terceiro" — NAO marcar
  Busca do sacado: botao lupa ao lado do #cnpj preenche sozinho no_sacado,
    endereco_cep, logradouro, numero, bairro, municipio, uf. Isso dispensa
    o popup de cadastro e a consulta de CNPJ na Receita que o portal antigo
    exigia — era o bloco mais fragil de todos.
  Prova de gravacao: a tela mostra "qtd. titulos: N" e "total: X"; a tabela
    ganha uma linha por titulo incluido.

Finalizacao ("Finalizar Operacao e Enviar") continua MANUAL, igual Firma e
FluxAsset — a automacao inclui os titulos e para.
"""
import asyncio
import os
import re

from config_manager import get_credencial


BASE_SIFAC = os.getenv("SIFAC_URL", "https://app.sifacweb.com.br/gcsecuritizadora")

# Tipo do titulo: DM = duplicata (definido com o usuario; transporte entra como DM)
TIPO_TITULO = os.getenv("SIFAC_TIPO", "DM")

CONFIRMA_POLLS = 25          # 25 x 200ms = 5s
BUSCA_SACADO_POLLS = 60      # 60 x 250ms = 15s (portal externo, margem larga)
GRAVACAO_POLLS = 30          # 30 x 400ms = 12s

DRY_RUN = os.getenv("GC_DRY_RUN", "") in ("1", "true", "True")


# ── JS ───────────────────────────────────────────────────────────────────────

# Contador da operacao: e' a PROVA de que o titulo entrou. Sem isso o codigo
# diria "salvo" com base em nada — foi o defeito que fez a analista ter de
# digitar faturas na mao no portal antigo.
JS_CONTADOR = r"""
() => {
    const t = document.body.innerText || '';
    const q = t.match(/qtd\.?\s*t[íi]tulos?\s*:?\s*(\d+)/i);
    const v = t.match(/total\s*:?\s*([\d.,]+)/i);
    let linhas = 0;
    for (const tr of document.querySelectorAll('table tbody tr')) {
        if (!tr.offsetParent) continue;
        const tds = [...tr.querySelectorAll('td')].map(x => (x.textContent||'').trim());
        if (tds.some(x => /^\d{3,10}$/.test(x))) linhas++;
    }
    return {qtd: q ? parseInt(q[1], 10) : null, total: v ? v[1] : null, linhas: linhas};
}
"""

JS_CLICAR_TEXTO = r"""
(args) => {
    const re = new RegExp(args.padrao, 'i');
    for (const b of document.querySelectorAll('button, a, input[type=button], input[type=submit]')) {
        if (!b.offsetParent) continue;
        const t = (b.textContent || b.value || '').trim();
        if (re.test(t)) { b.click(); return t; }
    }
    return null;
}
"""

# Lupa de busca do sacado: fica num input-group ao lado do #cnpj
JS_BUSCAR_SACADO = r"""
() => {
    const c = document.getElementById('cnpj');
    if (!c) return 'sem_campo_cnpj';
    let p = c.parentElement;
    for (let i = 0; i < 3 && p; i++) {
        const b = p.querySelector('button, a.btn, .input-group-text');
        if (b && b.offsetParent) { b.click(); return 'clicou'; }
        p = p.parentElement;
    }
    return 'sem_botao';
}
"""

JS_ESTADO_SACADO = r"""
() => {
    const g = (n) => {
        const el = document.querySelector(`#${n}, [name="${n}"]`);
        return el ? (el.value || '').trim() : '';
    };
    return {nome: g('no_sacado'), municipio: g('endereco_municipio'), uf: g('endereco_uf')};
}
"""

# Mensagem de erro/validacao que o portal exibe
JS_ERRO_VISIVEL = r"""
() => {
    const sels = ['.alert-danger', '.invalid-feedback', '.text-danger',
                  '.toast-error', '[class*="erro"]', '.swal2-html-container'];
    for (const s of sels) {
        for (const el of document.querySelectorAll(s)) {
            if (!el.offsetParent) continue;
            const t = (el.textContent || '').trim();
            if (t.length > 3) return t.slice(0, 200);
        }
    }
    return null;
}
"""


async def _log(status, msg):
    status.setdefault("logs", []).append(msg)


async def _contar(page):
    """(qtd_oficial, linhas). Tolerante: -1 se nao conseguiu ler."""
    try:
        r = await page.evaluate(JS_CONTADOR)
        return r.get("qtd"), (r.get("linhas") if r.get("linhas") is not None else -1)
    except Exception:
        return None, -1


# ── Login ────────────────────────────────────────────────────────────────────

async def fazer_login_sifac(page, sistema: str, status: dict | None = None):
    """Login no SIFAC. Matriz (G FREIRE) e SP (MORAIS) tem e-mails distintos,
    entao SEMPRE loga forcado — o portal e' o mesmo dominio pros dois."""
    log = (lambda m: status.setdefault("logs", []).append(m)) if status is not None else (lambda m: None)
    creds = get_credencial(sistema) or {}
    usuario = (creds.get("usuario") or "").strip()
    senha = creds.get("senha") or ""
    if not usuario or not senha:
        raise Exception(
            f"Credenciais do SIFAC ({sistema}) nao cadastradas. Acesse "
            f"Configuracoes e salve o e-mail e a senha do novo portal da GC."
        )
    if "@" not in usuario:
        raise Exception(
            f"Credencial do SIFAC ({sistema}) parece ser do portal ANTIGO "
            f"('{usuario}'). O SIFAC usa e-mail. Atualize em Configuracoes."
        )

    for tentativa in range(1, 4):
        try:
            await page.goto(f"{BASE_SIFAC}/Login", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1500)
            # Se ja esta logado com OUTRA filial, /Login costuma redirecionar.
            if "login" not in (page.url or "").lower():
                from services.cdp_tabs import limpar_sessao_dominio
                log(f"  [LOGIN] SIFAC ja tinha sessao — limpando pra entrar como {sistema}...")
                await limpar_sessao_dominio(page, "sifacweb.com.br")
                await page.goto(f"{BASE_SIFAC}/Login", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1500)

            await page.locator("#userName").wait_for(state="visible", timeout=20000)
            await page.fill("#userName", usuario)
            await page.fill("#password", senha)
            await page.evaluate(JS_CLICAR_TEXTO, {"padrao": r"^(entrar|login|acessar)$"})
            try:
                await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=40000)
            except Exception:
                pass
            await page.wait_for_timeout(2500)
            if "login" in (page.url or "").lower():
                erro = await page.evaluate(JS_ERRO_VISIVEL)
                raise Exception(f"SIFAC recusou o login{(' — ' + erro) if erro else ''}")
            log(f"  [LOGIN] SIFAC OK ({sistema})")
            return
        except Exception as e:
            if tentativa == 3:
                raise Exception(f"Login SIFAC ({sistema}) falhou apos 3 tentativas: {str(e)[:200]}")
            await page.wait_for_timeout(2000 * tentativa)


# ── Navegacao ────────────────────────────────────────────────────────────────

async def abrir_tela_operacao(page, status):
    """Vai pra Operacao > Incluir. Usa a ROTA DIRETA — o menu lateral e' um
    accordion e clicar nele quando ja esta aberto FECHA o submenu."""
    log = lambda m: status.setdefault("logs", []).append(m)
    await page.goto(f"{BASE_SIFAC}/Operacao/Incluir", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    try:
        await page.wait_for_function(
            """() => [...document.querySelectorAll('button,a')]
               .some(b => b.offsetParent && /incluir\\s*t[íi]tulo/i.test(b.textContent||''))""",
            timeout=25000,
        )
    except Exception:
        raise Exception(
            f"SIFAC: tela de operacao nao carregou (botao 'Incluir Titulo' ausente). "
            f"URL: {page.url[:90]}"
        )
    log("  [DIR] SIFAC — tela Operacao > Incluir aberta")


async def _abrir_modal_titulo(page):
    r = await page.evaluate(JS_CLICAR_TEXTO, {"padrao": r"incluir\s*t[íi]tulo"})
    if not r:
        raise Exception("SIFAC: botao 'Incluir Titulo' nao encontrado.")
    await page.locator("#nuDocumento").wait_for(state="visible", timeout=20000)


# ── Preenchimento de um titulo ───────────────────────────────────────────────

async def incluir_titulo_sifac(page, fatura, status):
    """Preenche o modal e clica Incluir. Retorna 'incluido' | 'dry_run'.
    Levanta excecao se o titulo NAO entrar na grid."""
    log = lambda m: status.setdefault("logs", []).append(m)
    numero = str(fatura.get("numero") or "?")

    # ── pre-check dos dados (antes de tocar no browser) ──
    doc = re.sub(r"\D", "", str(fatura.get("cliente_cnpj") or ""))
    if len(doc) not in (11, 14):
        raise Exception(f"SIFAC: fatura {numero} com CNPJ/CPF invalido ('{fatura.get('cliente_cnpj')}')")
    try:
        valor = float(fatura.get("valor") or 0)
    except Exception:
        valor = 0.0
    if valor <= 0:
        raise Exception(f"SIFAC: fatura {numero} com valor invalido ({fatura.get('valor')})")
    for campo in ("vencimento", "emissao"):
        if not re.match(r"^\d{2}/\d{2}/\d{4}$", str(fatura.get(campo) or "")):
            raise Exception(f"SIFAC: fatura {numero} com {campo} invalido ('{fatura.get(campo)}')")
    if len(numero) > 10:
        raise Exception(f"SIFAC: numero '{numero}' excede os 10 caracteres do campo Documento")

    qtd_antes, linhas_antes = await _contar(page)
    await _abrir_modal_titulo(page)

    # ── tipo e sacado (PJ pra CNPJ, PF pra CPF) ──
    await page.select_option("#tipo_recebivel", value=TIPO_TITULO)
    await page.select_option("#tipo_sacado", value=("PJ" if len(doc) == 14 else "PF"))

    # ── CNPJ + busca automatica do sacado ──
    await page.fill("#cnpj", doc)
    r = await page.evaluate(JS_BUSCAR_SACADO)
    if r != "clicou":
        log(f"  [WARN] Botao de busca do sacado nao encontrado ({r}) — tentando Tab")
        await page.press("#cnpj", "Tab")
    nome_sacado = ""
    for _ in range(BUSCA_SACADO_POLLS):
        await page.wait_for_timeout(250)
        st = await page.evaluate(JS_ESTADO_SACADO)
        if (st.get("nome") or "").strip():
            nome_sacado = st["nome"].strip()
            break
    if not nome_sacado:
        raise Exception(
            f"SIFAC: sacado do CNPJ {doc} nao retornou na busca do portal "
            f"(fatura {numero}). Pode nao estar cadastrado na GC — verifique manualmente."
        )
    log(f"  [OK] Sacado encontrado: {nome_sacado[:45]}")

    # ── demais campos ──
    valor_fmt = f"{valor:.2f}".replace(".", ",")
    chave = str(fatura.get("chave") or "").strip()
    if not chave:
        log(f"  [WARN] Fatura {numero}: sem chave NF-e")
    elif len(chave) != 44:
        log(f"  [WARN] Fatura {numero}: chave com {len(chave)} digitos (esperado 44)")

    valores = {
        "#nuDocumento": numero,
        "#vl_nominal": valor_fmt,
        "input[name='dt_emissao']": str(fatura.get("emissao")),
        "input[name='dt_vencimento']": str(fatura.get("vencimento")),
    }
    for sel, val in valores.items():
        await page.fill(sel, val)
    if chave:
        await page.fill("#cd_chavenfe", chave)

    # "Duplicata de Terceiro" deve ficar DESMARCADO
    try:
        if await page.is_checked("#fl_terceiro"):
            await page.uncheck("#fl_terceiro")
            log("  [INFO] Desmarquei 'Duplicata de Terceiro'")
    except Exception:
        pass

    # ── read-back: mascara nao pode deturpar o valor ──
    lidos = await page.evaluate("""(sels) => {
        const o = {};
        for (const s of sels) { const el = document.querySelector(s); o[s] = el ? el.value : null; }
        return o;
    }""", list(valores.keys()))
    so_dig = lambda s: re.sub(r"\D", "", s or "")
    for sel, esperado in valores.items():
        if so_dig(lidos.get(sel)) != so_dig(esperado):
            raise Exception(
                f"SIFAC: fatura {numero} — campo {sel} ficou '{lidos.get(sel)}' "
                f"(enviei '{esperado}'). Abortando ANTES de incluir."
            )
    _venc_lido = lidos.get("input[name='dt_vencimento']")
    log(f"  [OK] Campos conferidos: valor={lidos.get('#vl_nominal')} "
        f"venc={_venc_lido} doc={lidos.get('#nuDocumento')}")

    if DRY_RUN:
        log(f"  [DRY-RUN] Fatura {numero} preenchida e NAO incluida (GC_DRY_RUN=1)")
        return "dry_run"

    # ── Incluir ──
    r = await page.evaluate(JS_CLICAR_TEXTO, {"padrao": r"^incluir$"})
    if not r:
        raise Exception(f"SIFAC: botao 'Incluir' do modal nao encontrado (fatura {numero})")

    # confirmacao, se o portal pedir
    for _ in range(CONFIRMA_POLLS):
        await page.wait_for_timeout(200)
        try:
            c = await page.evaluate(JS_CLICAR_TEXTO, {"padrao": r"^(sim|ok|confirmar)$"})
        except Exception:
            break
        if c:
            log(f"  [INFO] Confirmacao clicada: '{c}'")
            break

    # ── PROVA DE GRAVACAO: o contador da operacao tem que subir ──
    entrou = False
    ultimo = (qtd_antes, linhas_antes)
    for _ in range(GRAVACAO_POLLS):
        try:
            await page.wait_for_timeout(400)
            qd, ld = await _contar(page)
        except Exception:
            continue
        ultimo = (qd, ld)
        # PROVA PRIMARIA: o contador do proprio portal. Numa operacao vazia
        # o texto "qtd. titulos" nao existe e qtd_antes vem None — tratar
        # como 0 faz o PRIMEIRO titulo tambem ser provado pelo contador, em
        # vez de depender da heuristica de linhas.
        if qd is not None:
            base = qtd_antes if qtd_antes is not None else 0
            if qd > base:
                entrou = True
                break
        # PROVA SECUNDARIA: so quando o contador nao e' legivel. Sozinha ela
        # e' fraca — JS_CONTADOR conta linha de QUALQUER tabela visivel, e
        # uma tabela que apareca por outro motivo inflaria o numero, dando um
        # "incluido" falso. Falso positivo e' o pior caso: a analista confia
        # que digitou e o titulo nao esta na operacao.
        elif ld > linhas_antes >= 0:
            entrou = True
            break
    if not entrou:
        erro = None
        try:
            erro = await page.evaluate(JS_ERRO_VISIVEL)
        except Exception:
            pass
        # Medido ao vivo: com chave NF-e INVALIDA o SIFAC recusa em silencio —
        # nao mostra mensagem nenhuma, so nao inclui. Por isso a chave entra
        # como principal suspeita quando nao ha erro visivel.
        pista = ""
        if not erro and chave:
            pista = (f" Nenhuma mensagem do portal — a causa mais provavel e' a "
                     f"chave NF-e ({len(chave)} digitos): o SIFAC valida a chave e "
                     f"recusa sem avisar quando ela nao confere.")
        raise Exception(
            f"SIFAC: titulo {numero} NAO entrou na operacao "
            f"(qtd {qtd_antes}->{ultimo[0]}, linhas {linhas_antes}->{ultimo[1]})"
            + (f". Portal: {erro}" if erro else pista)
            + " Precisa ser digitado manualmente."
        )
    log(f"  [OK] Titulo {numero} incluido (qtd {qtd_antes}->{ultimo[0]}) — {nome_sacado[:32]}")
    return "incluido"


# ── Core: loop ───────────────────────────────────────────────────────────────

async def _core_executar_gc_sifac(page, faturas_selecao, sistema, status):
    """Loop principal. `page` ja logada no SIFAC."""
    log = lambda m: status.setdefault("logs", []).append(m)
    faturas_dados = status.get("faturas_cache", {}) or {}
    status.setdefault("faturas_salvas", set())
    status["concluidas"] = status.get("concluidas", 0)

    dialogos = []

    def _on_dialog(d):
        dialogos.append(f"{d.type}: {d.message}")
        log(f"  [DIALOGO] {d.type}: {d.message[:120]}")
        asyncio.ensure_future(d.accept())

    page.on("dialog", _on_dialog)

    await abrir_tela_operacao(page, status)
    q0, l0 = await _contar(page)
    log(f"  [OK] Operacao aberta (qtd.titulos={q0}, {l0} linha(s))")
    if (q0 or 0) > 0:
        log(f"  [WARN] A operacao NAO esta vazia — os titulos serao somados aos {q0} existentes.")
    if DRY_RUN:
        log("  [DRY-RUN] GC_DRY_RUN=1 — preenche mas NAO inclui.")

    falhas: list = []
    total = len(faturas_selecao)
    for idx, sel in enumerate(faturas_selecao):
        numero = getattr(sel, "numero", None) or (sel.get("numero") if isinstance(sel, dict) else None)
        fatura = faturas_dados.get(numero)
        if not fatura:
            log(f"  [WARN] Fatura {numero}: dados nao encontrados no cache — PULADA")
            falhas.append((numero, "sem dados no cache"))
            continue
        log(f"[{idx + 1}/{total}] Incluindo fatura {numero} - {str(fatura.get('cliente_nome',''))[:35]}...")
        try:
            r = await incluir_titulo_sifac(page, fatura, status)
            if r == "incluido":
                status["concluidas"] = status.get("concluidas", 0) + 1
                status["faturas_salvas"].add(numero)
        except Exception as e:
            log(f"  [ERR] Fatura {numero}: {str(e)[:220]}")
            status.setdefault("erros", []).append(f"GC {sistema} fatura {numero}: {str(e)[:200]}")
            falhas.append((numero, str(e)[:160]))
            # fecha o modal pra nao travar a proxima
            try:
                await page.evaluate(JS_CLICAR_TEXTO, {"padrao": r"^fechar$"})
                await page.wait_for_timeout(800)
            except Exception:
                pass

    if dialogos:
        status.setdefault("erros", []).append(
            f"GC {sistema}: portal exibiu dialogo(s): {dialogos[:3]}")

    feitas = status.get("concluidas", 0)
    qf, lf = await _contar(page)
    if DRY_RUN:
        log(f"[DRY-RUN] GC {sistema}: {total} fatura(s) preenchida(s), nenhuma incluida.")
    elif feitas == total:
        log(f"[OK] GC {sistema} — {feitas}/{total} titulo(s) incluido(s). "
            f"Operacao com qtd.titulos={qf}. Pronto para 'Finalizar Operacao e Enviar'.")
    else:
        log(f"[WARN] GC {sistema} — PARCIAL: {feitas}/{total} titulo(s) incluido(s).")
        for num, motivo in falhas:
            log(f"     ↳ fatura {num} NAO incluida: {motivo}")
        msg = f"GC {sistema}: {feitas}/{total} titulo(s) incluido(s)."
        if falhas:
            msg += " Faltaram: " + ", ".join(str(n) for n, _ in falhas)
        status.setdefault("erros", []).append(msg)

    if not DRY_RUN and feitas == 0:
        log("  [ATENCAO] Nenhum titulo incluido — se uma operacao vazia ficou "
            "aberta no SIFAC, verifique na tela.")
    return status
