# -*- coding: utf-8 -*-
"""Digitacao direta de titulos na GC Recursos (substitui o fluxo do .rem).

Antes: gerava um .rem no GW (Exportar Boletos) e importava na GC via
"Importar Leiaute". 8 dos 10 bugs historicos da GC estavam nessa etapa.
Agora: digita campo a campo, igual a Firma e a FluxAsset.

CALIBRADO com medicao ao vivo (test_gc_sonda_digitacao.py, portal versao
202410151450). O que a sondagem provou:
  - Os 9 campos existem, visiveis, no modal "Cadastro de titulos":
    saca_id (CMC7/CPF/CNPJ), saca_nome (Nome), data_titu (Vencimento),
    valo_titu (Valor), nume_doct (Documento), nume_nota (Num.Nota),
    data_emis (Dt.Emissao), valo_nota (Vlr.Nota), chave_nf (Chave, maxlen 44)
  - Mascaras aceitam o formato da Firma: "1234,56" entra e volta identico
  - Lookup do sacado respondeu em 468ms (budget de 15s aqui = 32x de margem)
  - Salvar e Novo ficam no NIVEL 3 subindo de #valo_titu, dentro do modal
  - Nenhum dialogo nativo (window.confirm) — a confirmacao e' DOM
  - Aba ativa ao abrir e' "Importacao"; precisa clicar "Digitacao"

Nao toca em firma_automation.py, fluxasset_automation.py nem no fluxo .rem
(que continua no gc_automation.py como rollback via GC_MODO=remessa).
"""
import asyncio
import os
import re
import traceback

from config_manager import get_credencial  # noqa: F401  (paridade com os outros modulos)
from services.firma_automation import buscar_dados_cnpj  # reusa: 3 APIs em paralelo
from _tz import now_br


GC_URL = "http://gcrecursos.dyndns.org:9000/FactaConsult"
MODAL = ".modal-interna-fundo"

# ── Budgets calibrados pela sondagem ─────────────────────────────────────────
# Lookup medido: 468ms. Damos 15s de teto: o host da GC e' HTTP na porta 9000
# via DynDNS (o login dela precisa de 90s de timeout), entao a margem larga
# protege contra picos sem transformar lentidao em "sacado nao cadastrado".
LOOKUP_ESPERA_MIN_MS = 300
LOOKUP_POLLS = 150          # 150 x 100ms = 15s
CONFIRMA_POLLS = 25         # 25 x 200ms = 5s (igual Firma/Flux)
CAMPOS_TITULO = ("#data_titu", "#valo_titu", "#nume_doct", "#nume_nota",
                 "#data_emis", "#valo_nota", "#chave_nf")
IDS_OBRIGATORIOS = ["saca_id", "data_titu", "valo_titu", "nume_doct", "data_emis"]

# Preenche tudo mas NAO clica Salvar — pra conferir com o olho antes.
DRY_RUN = os.getenv("GC_DRY_RUN", "") in ("1", "true", "True")


def _data_operacao_str() -> str:
    """Data que a GC registra a operacao (mesma regra da Firma: rollover de
    fim de semana, fuso de Brasilia porque o Railway roda em UTC)."""
    d = now_br()
    wd = d.weekday()
    if wd == 5:      # sabado -> segunda
        from datetime import timedelta
        d = d + timedelta(days=2)
    elif wd == 6:    # domingo -> segunda
        from datetime import timedelta
        d = d + timedelta(days=1)
    return d.strftime("%d/%m/%Y")


# ── JS reutilizavel ──────────────────────────────────────────────────────────

# Acha o botao do FORMULARIO subindo do campo-ancora e PARANDO no modal.
# A Firma busca no document inteiro (firma_automation.py:455-461); na GC isso
# pegaria o "Pesquisar"/"Acoes" da listagem, que a sondagem achou no nivel 9
# FORA do modal. Aqui o teto e' o proprio modal.
JS_CLICAR_BOTAO_DO_FORM = """
(args) => {
    const ancora = document.getElementById(args.ancora);
    if (!ancora) return 'ancora_nao_existe';
    const teto = ancora.closest(args.modal) || document.body;
    let el = ancora, nivel = 0;
    while (el && nivel < 12) {
        for (const b of el.querySelectorAll('button, input[type=button], input[type=submit]')) {
            if (!b.offsetParent) continue;
            const t = (b.textContent || b.value || '').trim();
            if (t === args.texto) { b.click(); return 'clicado_nivel_' + nivel; }
        }
        if (el === teto) break;          // nao sai do modal
        el = el.parentElement; nivel++;
    }
    return 'nao_encontrado';
}
"""

# Botoes de CONFIRMACAO (Sim/Ok) vivem no ULTIMO modal visivel — escopo
# diferente do formulario. Misturar os dois e' como se clica no botao errado.
JS_CLICAR_CONFIRMACAO = """
(texto) => {
    const modais = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
    const escopos = modais.length ? [modais[modais.length - 1]] : [document.body];
    for (const esc of escopos) {
        for (const b of esc.querySelectorAll('button, input[type=button], input[type=submit]')) {
            if (!b.offsetParent) continue;
            const t = (b.textContent || b.value || '').trim();
            if (t === texto) { b.click(); return true; }
        }
    }
    return false;
}
"""

JS_ATIVAR_ABA = """
(nomes) => {
    for (const li of document.querySelectorAll('.aba-cabecalho-lista-li')) {
        const t = (li.textContent || '').trim();
        if (nomes.includes(t) && li.offsetParent) { li.click(); return t; }
    }
    return null;
}
"""

JS_ESTADO_LOOKUP = """
() => {
    const n = document.getElementById('saca_nome');
    const nome = n ? (n.value || '').trim() : '';
    let popup = false;
    for (const m of document.querySelectorAll('.modal-interna-fundo')) {
        if (!m.offsetParent) continue;
        const tit = m.querySelector('.modal-titulo');
        if (tit && /cadastro de sacado/i.test(tit.textContent || '')) { popup = true; break; }
    }
    return {nome: nome, popup: popup};
}
"""


async def _log_status(status, msg):
    status.setdefault("logs", []).append(msg)


# ── Navegacao ────────────────────────────────────────────────────────────────

async def navegar_para_digitacao_gc(page, status):
    """Vai pra tela de Digitacao. Sondagem confirmou que o link do menu
    funciona (href='/FactaConsult/#/operacao/digitacao'), mas a GC era a unica
    das 3 factories SEM fallback de URL direta — aqui ela ganha um."""
    log = lambda m: status.setdefault("logs", []).append(m)
    via_link = await page.evaluate("""() => {
        const a = document.querySelector('a[href*="/operacao/digitacao"]');
        if (!a) return false;
        a.click(); return true;
    }""")
    if via_link:
        try:
            await page.wait_for_function(
                """() => [...document.querySelectorAll('button')]
                   .some(b => b.offsetParent && b.textContent.trim() === 'Novo')""",
                timeout=12000,
            )
            return
        except Exception:
            pass
    # Fallback: URL direta (SPA com rota hash)
    log("  [DIR] Link do menu nao levou — navegando direto pra /operacao/digitacao")
    await page.goto(f"{GC_URL}/#/operacao/digitacao", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_function(
        """() => [...document.querySelectorAll('button')]
           .some(b => b.offsetParent && b.textContent.trim() === 'Novo')""",
        timeout=20000,
    )


async def _abrir_modal_novo(page):
    """Clica o 'Novo' da LISTAGEM (fora do modal) pra abrir o formulario."""
    ok = await page.evaluate("""() => {
        for (const b of document.querySelectorAll('button')) {
            if (b.offsetParent && b.textContent.trim() === 'Novo') { b.click(); return true; }
        }
        return false;
    }""")
    if not ok:
        raise Exception("GC: botao 'Novo' nao encontrado na tela de digitacao.")
    await page.wait_for_selector(f"{MODAL} .modal-titulo", timeout=20000)


async def _ativar_aba_digitacao(page):
    """A aba ativa ao abrir o modal e' 'Importacao' — precisa trocar."""
    aba = await page.evaluate(JS_ATIVAR_ABA, ["Digitação", "Digitacao"])
    if not aba:
        raise Exception("GC: aba 'Digitacao' nao encontrada no modal de titulos.")
    await page.locator("#saca_id").first.wait_for(state="visible", timeout=15000)
    return aba


async def _assert_form_ok(page, status):
    """G1 — guard de formulario. Falha com mensagem que NOMEIA o campo em vez
    de estourar timeout obscuro 5 campos depois."""
    faltando = await page.evaluate("""(ids) => {
        const out = [];
        for (const id of ids) {
            const el = document.getElementById(id);
            if (!el || el.offsetParent === null) out.push(id);
        }
        return out;
    }""", IDS_OBRIGATORIOS)
    if faltando:
        raise Exception(
            f"GC: formulario de digitacao diferente do esperado — campos "
            f"ausentes/invisiveis: {faltando}. O portal pode ter mudado. Rode "
            f"test_gc_sonda_digitacao.py e compare com backend/debug_gc_digitacao.json."
        )


# ── Lookup do sacado ─────────────────────────────────────────────────────────

async def aguardar_lookup_sacado_gc(page, status):
    """G3 — TRES desfechos, nao dois: 'encontrado' | 'popup_cadastro' | 'timeout'.

    A Firma trata timeout como sucesso (firma_automation.py:227-232). Na GC isso
    seria perigoso: servidor lento -> o codigo abriria o popup de cadastro pra um
    CNPJ que JA existe -> sacado duplicado na base da GC. Lookup medido: 468ms.
    """
    await page.wait_for_timeout(LOOKUP_ESPERA_MIN_MS)
    for _ in range(LOOKUP_POLLS):
        st = await page.evaluate(JS_ESTADO_LOOKUP)
        if st.get("popup"):
            return "popup_cadastro"
        nome = (st.get("nome") or "").strip()
        if nome and nome.lower() != "undefined" and len(nome) > 1:
            return "encontrado"
        await page.wait_for_timeout(100)
    # Ultima chance: o popup pode ter aberto no ultimo instante
    st = await page.evaluate(JS_ESTADO_LOOKUP)
    if st.get("popup"):
        return "popup_cadastro"
    return "timeout"


async def cadastrar_sacado_gc(page, cnpj_limpo, fatura, status):
    """Popup 'Cadastro de sacado'. Mesmos IDs da Firma (produto Facta).
    Este bloco e' o menos exercitado na GC — por isso valida cada passo."""
    log = lambda m: status.setdefault("logs", []).append(m)
    log(f"  [INFO] Sacado {cnpj_limpo} nao cadastrado na GC — cadastrando...")

    dados = {}
    try:
        dados = await buscar_dados_cnpj(cnpj_limpo) or {}
    except Exception as e:
        log(f"  [WARN] Consulta CNPJ falhou: {str(e)[:100]}")
    nome_usar = dados.get("nome") or fatura.get("cliente_nome") or ""
    if not nome_usar:
        raise Exception(f"GC: sem nome pro sacado {cnpj_limpo} (API e planilha vazias)")
    primeiro = (nome_usar.split() or ["cliente"])[0].lower()
    primeiro = re.sub(r"[^a-z0-9]", "", primeiro) or "cliente"

    await page.locator("#iden").first.wait_for(state="visible", timeout=10000)
    await page.fill("#iden", cnpj_limpo)
    await page.fill("#nome", nome_usar)

    if dados.get("cep"):
        try:
            await page.fill("#cep", re.sub(r"\D", "", dados["cep"]))
            await page.press("#cep", "Tab")
            try:
                await page.wait_for_function(
                    "() => { const e = document.getElementById('ende'); return e && e.value.length > 0; }",
                    timeout=4000)
            except Exception:
                await page.wait_for_timeout(500)
        except Exception:
            pass

    # Preenche o resto SO se o autocomplete do CEP nao preencheu
    for sel, chave in (("#ende", "endereco"), ("#nume", "numero"), ("#bair", "bairro"),
                       ("#cida", "cidade"), ("#uf", "uf")):
        val = dados.get(chave)
        if not val:
            continue
        try:
            atual = await page.input_value(sel, timeout=2000)
            if not (atual or "").strip():
                await page.fill(sel, str(val))
        except Exception:
            pass
    try:
        await page.fill("#e_mail", f"{primeiro}@gmail.com")
    except Exception:
        pass

    # Salvar do POPUP: ancora em #iden, teto no modal do popup
    res = await page.evaluate(JS_CLICAR_BOTAO_DO_FORM,
                              {"ancora": "iden", "modal": MODAL, "texto": "Salvar"})
    log(f"  [INFO] Salvar do cadastro de sacado: {res}")
    if res in ("nao_encontrado", "ancora_nao_existe"):
        raise Exception(f"GC: nao achei o botao Salvar do popup de cadastro ({res})")

    # Resposta: "Confirma salvar?" ou "CNPJ/CPF Invalido"
    await page.wait_for_timeout(700)
    corpo = await page.evaluate("() => document.body.innerText || ''")
    if re.search(r"CNPJ/CPF\s*Inv", corpo, re.I):
        await page.evaluate(JS_CLICAR_CONFIRMACAO, "Ok")
        raise Exception(
            f"GC: CNPJ {cnpj_limpo} rejeitado como invalido pelo portal — "
            f"verifique os digitos verificadores na planilha"
        )
    if "confirma salvar" in corpo.lower():
        await page.evaluate(JS_CLICAR_CONFIRMACAO, "Sim")

    # Espera o popup fechar
    for _ in range(40):
        await page.wait_for_timeout(200)
        st = await page.evaluate(JS_ESTADO_LOOKUP)
        if not st.get("popup"):
            break

    # Re-lookup: o portal nao revincula o sacado sozinho
    for tentativa in range(1, 4):
        st = await page.evaluate(JS_ESTADO_LOOKUP)
        if (st.get("nome") or "").strip():
            log(f"  [OK] Sacado cadastrado e vinculado ({st['nome'][:40]})")
            return
        await page.wait_for_timeout(1200 * tentativa)
        try:
            await page.fill("#saca_id", "")
            await page.wait_for_timeout(250)
            await page.fill("#saca_id", cnpj_limpo)
            await page.press("#saca_id", "Tab")
        except Exception:
            pass
        if await aguardar_lookup_sacado_gc(page, status) == "encontrado":
            log(f"  [OK] Sacado vinculado na tentativa {tentativa}")
            return
    raise Exception(
        f"GC: sacado {cnpj_limpo} salvo no cadastro mas nao vinculou no "
        f"formulario (#saca_nome vazio apos 3 re-lookups)"
    )


# ── Preenchimento do titulo ──────────────────────────────────────────────────

async def preencher_titulo_gc(page, fatura, status):
    """Preenche e salva UM titulo. Ordem obrigatoria: CNPJ primeiro (dispara o
    lookup e pode abrir o popup), campos do titulo depois."""
    log = lambda m: status.setdefault("logs", []).append(m)
    numero = fatura.get("numero") or "?"

    # G2 — pre-check dos dados antes de tocar no browser
    cnpj_limpo = re.sub(r"\D", "", str(fatura.get("cliente_cnpj") or ""))
    if len(cnpj_limpo) not in (11, 14):
        raise Exception(f"GC: fatura {numero} com CNPJ/CPF invalido ('{fatura.get('cliente_cnpj')}')")
    try:
        valor = float(fatura.get("valor") or 0)
    except Exception:
        valor = 0.0
    if valor <= 0:
        raise Exception(f"GC: fatura {numero} com valor invalido ({fatura.get('valor')})")
    for campo in ("vencimento", "emissao"):
        v = str(fatura.get(campo) or "")
        if not re.match(r"^\d{2}/\d{2}/\d{4}$", v):
            raise Exception(f"GC: fatura {numero} com {campo} invalido ('{v}')")

    await _assert_form_ok(page, status)

    # 1. CNPJ -> lookup
    await page.locator("#saca_id").first.wait_for(state="visible", timeout=10000)
    await page.fill("#saca_id", cnpj_limpo)
    await page.press("#saca_id", "Tab")
    desfecho = await aguardar_lookup_sacado_gc(page, status)

    if desfecho == "timeout":
        # NAO abre cadastro: evita duplicar sacado que talvez ja exista
        raise Exception(
            f"GC: servidor nao respondeu ao lookup do CNPJ {cnpj_limpo} em "
            f"{LOOKUP_POLLS // 10}s — fatura {numero} nao digitada (evitando "
            f"cadastro duplicado de sacado)"
        )
    if desfecho == "popup_cadastro":
        await cadastrar_sacado_gc(page, cnpj_limpo, fatura, status)

    await page.wait_for_timeout(400)

    # 2. Campos do titulo. Mascaras validadas: "1234,56" entra literal.
    valor_fmt = f"{valor:.2f}".replace(".", ",")
    chave = str(fatura.get("chave") or "").strip()
    if not chave:
        log(f"  [WARN] Fatura {numero}: sem chave NF-e — campo Chave ficara vazio")
    elif len(chave) != 44:
        log(f"  [WARN] Fatura {numero}: chave com {len(chave)} digitos (esperado 44)")

    valores = {
        "#data_titu": str(fatura.get("vencimento") or ""),
        "#valo_titu": valor_fmt,
        "#nume_doct": str(numero),
        "#nume_nota": str(numero),
        "#data_emis": str(fatura.get("emissao") or ""),
        "#valo_nota": valor_fmt,
        "#chave_nf": chave,
    }
    for sel in CAMPOS_TITULO:
        val = valores.get(sel) or ""
        if not val:
            continue
        try:
            await page.locator(sel).first.wait_for(state="visible", timeout=4000)
            await page.fill(sel, val)
        except Exception as e:
            log(f"  [WARN] Campo {sel} nao preenchido: {str(e)[:80]}")

    # 3. G5 — read-back ANTES de salvar. Mascara que deturpe o valor seria um
    #    titulo com valor errado numa operacao de factoring.
    lidos = await page.evaluate("""(sels) => {
        const out = {};
        for (const s of sels) {
            const el = document.querySelector(s);
            out[s] = el ? el.value : null;
        }
        return out;
    }""", list(CAMPOS_TITULO))
    so_dig = lambda s: re.sub(r"\D", "", s or "")
    for sel in ("#valo_titu", "#data_titu", "#nume_doct", "#data_emis"):
        esperado, lido = valores.get(sel) or "", lidos.get(sel) or ""
        if not esperado:
            continue
        if so_dig(lido) != so_dig(esperado):
            raise Exception(
                f"GC: fatura {numero} — campo {sel} nao ficou com o valor certo "
                f"(enviei '{esperado}', o campo contem '{lido}'). Abortando ANTES "
                f"de salvar pra nao gravar dado errado."
            )
    log(f"  [OK] Campos conferidos: valor={lidos.get('#valo_titu')} "
        f"venc={lidos.get('#data_titu')} doc={lidos.get('#nume_doct')}")

    if DRY_RUN:
        log(f"  [DRY-RUN] Fatura {numero} preenchida e NAO salva "
            f"(GC_DRY_RUN=1). Confira na tela e salve manualmente se quiser.")
        return "dry_run"

    # 4. Salvar (ancora #valo_titu, teto no modal — medido no nivel 3)
    res = await page.evaluate(JS_CLICAR_BOTAO_DO_FORM,
                              {"ancora": "valo_titu", "modal": MODAL, "texto": "Salvar"})
    log(f"  [INFO] Salvar do titulo: {res}")
    if res in ("nao_encontrado", "ancora_nao_existe"):
        raise Exception(f"GC: nao achei o botao Salvar do formulario ({res})")

    # 5. "Confirma salvar?" — DOM (sondagem: nenhum dialogo nativo)
    for _ in range(CONFIRMA_POLLS):
        await page.wait_for_timeout(200)
        corpo = await page.evaluate("() => document.body.innerText || ''")
        if "confirma salvar" in corpo.lower():
            if await page.evaluate(JS_CLICAR_CONFIRMACAO, "Sim"):
                log("  [INFO] Confirmacao 'Sim' aceita")
                break
    await page.wait_for_timeout(900)

    # 6. Erro de validacao pos-save
    erro = await page.evaluate("""() => {
        const sels = ['.alert-danger','.error','.msg-erro','[class*="erro"]',
                      '[class*="error"]','.toast-error','.notification-error'];
        for (const s of sels) {
            for (const el of document.querySelectorAll(s)) {
                if (el.offsetParent && (el.textContent || '').trim().length > 3) {
                    return (el.textContent || '').trim().slice(0, 200);
                }
            }
        }
        return null;
    }""")
    if erro:
        raise Exception(f"GC: titulo {numero} nao foi salvo — portal retornou: {erro}")
    log(f"  [OK] Titulo {numero} salvo ({fatura.get('cliente_nome', '')[:35]})")
    return "salvo"


# ── Core: loop de faturas ────────────────────────────────────────────────────

async def _core_executar_gc_digitacao(page, faturas_selecao, sistema, status):
    """Loop principal. `page` ja logada na GC. Mesma forma do
    _core_executar_firma: erro numa fatura nao aborta o lote."""
    log = lambda m: status.setdefault("logs", []).append(m)
    faturas_dados = status.get("faturas_cache", {}) or {}
    status.setdefault("faturas_salvas", set())
    status["concluidas"] = status.get("concluidas", 0)

    # G4 — dialogo nativo. A sondagem nao viu nenhum, mas se aparecer e o
    # Playwright auto-dismissar, o Salvar seria cancelado em silencio.
    dialogos = []

    def _on_dialog(d):
        dialogos.append(f"{d.type}: {d.message}")
        log(f"  [DIALOGO NATIVO] {d.type}: {d.message[:120]}")
        asyncio.ensure_future(d.accept())

    page.on("dialog", _on_dialog)

    log("[DIR] GC — navegando para Digitacao...")
    await navegar_para_digitacao_gc(page, status)
    await _abrir_modal_novo(page)
    aba = await _ativar_aba_digitacao(page)
    log(f"  [OK] Modal aberto, aba '{aba}' ativa")
    if DRY_RUN:
        log("  [DRY-RUN] GC_DRY_RUN=1 — vou preencher mas NAO salvar nenhum titulo.")

    total = len(faturas_selecao)
    for idx, sel in enumerate(faturas_selecao):
        numero = getattr(sel, "numero", None) or (sel.get("numero") if isinstance(sel, dict) else None)
        fatura = faturas_dados.get(numero)
        if not fatura:
            log(f"  [WARN] Dados nao encontrados para fatura {numero}")
            continue
        log(f"[{idx + 1}/{total}] Digitando fatura {numero} - {fatura.get('cliente_nome', '')[:35]}...")

        if idx > 0:
            # 'Novo' do FORM (nivel 3 de #valo_titu, dentro do modal) —
            # nao o 'Novo' da listagem, que resetaria a tela.
            res = await page.evaluate(JS_CLICAR_BOTAO_DO_FORM,
                                      {"ancora": "valo_titu", "modal": MODAL, "texto": "Novo"})
            log(f"  [INFO] Novo do form: {res}")
            if res in ("nao_encontrado", "ancora_nao_existe"):
                # Fallback: fecha e reabre o modal
                log("  [INFO] 'Novo' do form nao achado — reabrindo modal")
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(600)
                    await _abrir_modal_novo(page)
                    await _ativar_aba_digitacao(page)
                except Exception as e:
                    status.setdefault("erros", []).append(f"GC {sistema}: nao consegui reabrir o form: {e}")
                    break
            else:
                try:
                    await page.locator("#saca_id").first.wait_for(state="visible", timeout=8000)
                except Exception:
                    await _ativar_aba_digitacao(page)

        try:
            r = await preencher_titulo_gc(page, fatura, status)
            if r == "salvo":
                status["concluidas"] = status.get("concluidas", 0) + 1
                status["faturas_salvas"].add(numero)
        except Exception as e:
            log(f"  [ERR] Fatura {numero}: {str(e)[:220]}")
            status.setdefault("erros", []).append(f"GC {sistema} fatura {numero}: {str(e)[:200]}")

    if dialogos:
        status.setdefault("erros", []).append(
            f"GC {sistema}: portal exibiu dialogo(s) nativo(s) — verifique se os "
            f"titulos foram salvos: {dialogos[:3]}"
        )

    # Deixa a tela na listagem, pronta pro usuario definir conta corrente
    # e encaminhar (finalizacao e' MANUAL por decisao de projeto).
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(700)
        await page.evaluate("""() => {
            for (const b of document.querySelectorAll('button')) {
                if (b.offsetParent && b.textContent.trim() === 'Pesquisar') { b.click(); return; }
            }
        }""")
        await page.wait_for_timeout(1500)
        log("  [OK] Tela na listagem de operacoes — pronto para 'Definir conta corrente' + 'Encaminhar'")
    except Exception as e:
        log(f"  [WARN] Nao consegui voltar pra listagem: {str(e)[:100]}")

    feitas = status.get("concluidas", 0)
    # Efeito colateral descoberto no teste ao vivo: o clique em 'Novo' da
    # listagem JA CRIA uma operacao (Sequencial) na GC. Se nenhum titulo foi
    # digitado, sobra uma operacao vazia ("0 titulos / Nao finalizado") poluindo
    # a listagem. Avisa explicitamente em vez de deixar passar em silencio.
    if not DRY_RUN and feitas == 0:
        log(f"  [ATENCAO] Nenhum titulo foi digitado — a GC criou uma operacao "
            f"VAZIA nesta tentativa. Exclua-a na listagem (Acoes > Excluir) "
            f"pra nao acumular registros em branco.")
        status.setdefault("erros", []).append(
            f"GC {sistema}: operacao criada mas nenhum titulo digitado — "
            f"exclua a operacao vazia na listagem da GC.")

    if DRY_RUN:
        log(f"[DRY-RUN] GC {sistema}: {total} fatura(s) preenchida(s), nenhuma salva.")
    elif feitas == total:
        log(f"[OK] GC {sistema} — {feitas}/{total} titulo(s) digitado(s).")
    else:
        log(f"[WARN] GC {sistema} — PARCIAL: {feitas}/{total} titulo(s) digitado(s).")
    return status
