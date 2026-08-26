# -*- coding: utf-8 -*-
"""SONDAGEM READ-ONLY do formulario de digitacao da GC Recursos.

Objetivo: descobrir se o formulario de digitacao da GC e' igual ao da Firma
ANTES de escrever o codigo de producao. Sem essa medicao o codigo nasce de
palpite: Firma e FluxAsset tem 35/35 seletores identicos e ainda assim
precisaram de budgets de espera diferentes (2,5s vs 5,3s).

GARANTIAS (travas duras):
  - NUNCA clica Salvar / Sim / Confirmar / Enviar / Importar / Excluir /
    Encaminhar / Definir conta -> _clicar_seguro() aborta se o texto casar.
  - NUNCA faz set_input_files (nao importa .rem).
  - NUNCA faz login nem limpa sessao -> conecta via CDP na aba que VOCE
    ja deixou logada. Voce nao e' deslogado.
  - Fase A e' 100% passiva. Fases B/C so rodam com --eu-confirmo.

USO:
  python test_gc_sonda_digitacao.py
  python test_gc_sonda_digitacao.py --eu-confirmo --cnpj-ok 12345678000199 --cnpj-novo 98765432000188

Saida: backend/debug_gc_digitacao.json (+ .png)
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "backend")
os.environ["PYTHONIOENCODING"] = "utf-8"

from playwright.async_api import async_playwright

CDP_URL = os.getenv("CDP_URL", "http://localhost:9222")
GC_MARKER = "gcrecursos.dyndns.org"
GC_URL = "http://gcrecursos.dyndns.org:9000/FactaConsult"
OUT_JSON = Path("backend/debug_gc_digitacao.json")
OUT_PNG = Path("backend/debug_gc_digitacao.png")

# Trava dura: a sondagem nao escreve no portal.
SALVAR = False

CLIQUES_PROIBIDOS = (
    "salvar", "sim", "confirmar", "enviar", "importar",
    "excluir", "encaminhar", "definir conta",
)

# Os 9 IDs que a digitacao da Firma usa (7 do titulo + saca_id + saca_nome)
IDS_ALVO = [
    "saca_id", "saca_nome",
    "data_titu", "valo_titu", "nume_doct", "nume_nota",
    "data_emis", "valo_nota", "chave_nf",
]

RESULTADO = {"etapas": [], "dialogos_nativos": [], "erro": None}


def etapa(nome, dados):
    RESULTADO["etapas"].append({"etapa": nome, "dados": dados})
    print("\n=== %s ===" % nome)
    print(json.dumps(dados, ensure_ascii=False, indent=2)[:2500])


async def _clicar_seguro(page, texto_exato, descricao=""):
    """Clica botao por texto EXATO, abortando se estiver na lista negra."""
    if any(p in texto_exato.lower() for p in CLIQUES_PROIBIDOS):
        raise SystemExit(
            "ABORTADO: a sondagem tentou clicar '%s' (%s) - esta na lista negra. "
            "Isso e um bug do script." % (texto_exato, descricao)
        )
    return await page.evaluate(
        """(txt) => {
            for (const b of document.querySelectorAll('button, input[type=button], input[type=submit], a')) {
                if (!b.offsetParent) continue;
                const t = (b.textContent || b.value || '').trim();
                if (t === txt) { b.click(); return true; }
            }
            return false;
        }""",
        texto_exato,
    )


JS_DUMP_CAMPOS = """
(ids) => {
    const out = {};
    for (const id of ids) {
        const el = document.getElementById(id);
        if (!el) { out[id] = "NAO_EXISTE"; continue; }
        const r = el.getBoundingClientRect();
        let label = '';
        try {
            const lb = document.querySelector('label[for="' + id + '"]');
            if (lb) label = (lb.textContent || '').trim();
            if (!label) {
                let p = el.parentElement;
                for (let i = 0; i < 3 && p; i++) {
                    const l = p.querySelector('label');
                    if (l) { label = (l.textContent || '').trim(); break; }
                    p = p.parentElement;
                }
            }
        } catch (e) {}
        const modal = el.closest('.modal-interna-fundo');
        let tituloModal = null;
        if (modal) {
            const tm = modal.querySelector('.modal-titulo');
            tituloModal = tm ? (tm.textContent || '').trim() : null;
        }
        const frm = el.closest('form');
        out[id] = {
            existe: true,
            tag: el.tagName.toLowerCase(),
            type: el.type || null,
            name: el.name || null,
            placeholder: el.placeholder || null,
            maxlength: el.getAttribute('maxlength'),
            disabled: !!el.disabled,
            readonly: !!el.readOnly,
            required: !!el.required,
            visivel: el.offsetParent !== null,
            rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
            value: el.value,
            className: el.className,
            label: label,
            dentro_de_modal: !!modal,
            titulo_modal_pai: tituloModal,
            form_id: frm ? frm.id : null,
            outerHTML: el.outerHTML.slice(0, 250)
        };
    }
    return out;
}
"""

JS_INVENTARIO = """
() => {
    const escopo = document.querySelector('.modal-interna-fundo') || document.body;
    const out = [];
    for (const el of escopo.querySelectorAll('input, select, textarea')) {
        if (!el.offsetParent) continue;
        if (el.type === 'hidden') continue;
        let label = '';
        try {
            if (el.id) {
                const lb = document.querySelector('label[for="' + el.id + '"]');
                if (lb) label = (lb.textContent || '').trim();
            }
        } catch (e) {}
        const item = {
            id: el.id || null,
            name: el.name || null,
            tag: el.tagName.toLowerCase(),
            type: el.type || null,
            placeholder: el.placeholder || null,
            maxlength: el.getAttribute('maxlength'),
            required: !!el.required,
            class_tem_obrigatorio: /obrigat|required/i.test(el.className || ''),
            label: label,
            value_default: el.value
        };
        if (el.tagName === 'SELECT') {
            item.opcoes = [...el.options].slice(0, 15).map(o => o.text.trim());
        }
        out.push(item);
    }
    return out;
}
"""

JS_BOTOES_NIVEL = """
(ancoraId) => {
    const ancora = document.getElementById(ancoraId);
    if (!ancora) return {erro: 'ancora ' + ancoraId + ' nao existe', botoes: []};
    const achados = [];
    const vistos = new Set();
    let el = ancora, nivel = 0;
    while (el && nivel < 25) {
        for (const b of el.querySelectorAll('button, input[type=button], input[type=submit]')) {
            if (!b.offsetParent) continue;
            const t = (b.textContent || b.value || '').trim();
            const chave = t + '|' + (b.title || '');
            if (!t || vistos.has(chave)) continue;
            vistos.add(chave);
            const r = b.getBoundingClientRect();
            const md = b.closest('.modal-interna-fundo');
            let tm = null;
            if (md) {
                const q = md.querySelector('.modal-titulo');
                tm = q ? (q.textContent || '').trim() : null;
            }
            achados.push({
                texto: t, title: b.title || null, nivel: nivel,
                className: b.className,
                rect: {x: Math.round(r.x), y: Math.round(r.y)},
                dentro_modal: !!md, titulo_modal: tm
            });
        }
        el = el.parentElement; nivel++;
    }
    return {ancora: ancoraId, botoes: achados};
}
"""

JS_MODAIS_ABAS = """
() => {
    const modais = [...document.querySelectorAll('.modal-interna-fundo')]
        .filter(m => m.offsetParent).map(m => {
            const tm = m.querySelector('.modal-titulo');
            return {
                titulo: tm ? (tm.textContent || '').trim() : '',
                abas: [...m.querySelectorAll('.aba-cabecalho-lista-li')].map(li => ({
                    texto: (li.textContent || '').trim(),
                    classes: li.className,
                    ativa: /ativ/i.test(li.className),
                    visivel: li.offsetParent !== null,
                    disabled: li.getAttribute('aria-disabled') === 'true' || li.classList.contains('disabled')
                }))
            };
        });
    const abas_globais = [...document.querySelectorAll('.aba-cabecalho-lista-li')].map(li => ({
        texto: (li.textContent || '').trim(),
        classes: li.className,
        ativa: /ativ/i.test(li.className),
        visivel: li.offsetParent !== null
    }));
    return {modais_visiveis: modais, abas_no_documento: abas_globais,
            body_class: document.body.className};
}
"""

JS_LINHAS_ACOES = """
() => {
    const out = [];
    let escopos = [...document.querySelectorAll('.modal-interna-fundo')].filter(m => m.offsetParent);
    if (!escopos.length) escopos = [document.body];
    for (const esc of escopos) {
        for (const tr of esc.querySelectorAll('tbody tr')) {
            if (!tr.offsetParent) continue;
            const cels = [...tr.querySelectorAll('td')].map(td => (td.textContent || '').trim());
            const btns = [...tr.querySelectorAll('button, a, i')].filter(b => b.offsetParent)
                .map(b => ({texto: (b.textContent || '').trim(), title: b.title || null, className: b.className}))
                .filter(b => b.texto || b.title);
            if (btns.length) out.push({celulas: cels.slice(0, 8), botoes: btns});
        }
    }
    return out.slice(0, 10);
}
"""

JS_GRID_STATUS = """
() => {
    const cab = [...document.querySelectorAll('table thead th, table tr:first-child th')]
        .map(th => (th.textContent || '').trim()).filter(Boolean);
    const linhas = [];
    for (const tr of document.querySelectorAll('table tbody tr, table tr')) {
        if (!tr.offsetParent) continue;
        const tds = [...tr.querySelectorAll('td')].map(td => (td.textContent || '').trim());
        if (tds.length) linhas.push(tds);
    }
    const curtos = new Set();
    for (const l of linhas) {
        for (const c of l) {
            if (c && c.length < 22 && !/^[0-9.,\\/\\s-]+$/.test(c)) curtos.add(c);
        }
    }
    return {
        cabecalho: cab,
        primeiras_linhas: linhas.slice(0, 6),
        valores_texto_distintos: [...curtos].slice(0, 40),
        inputs_filtro: [...document.querySelectorAll('input[type=text]')].slice(0, 4)
            .map(i => ({id: i.id || null, name: i.name || null,
                        placeholder: i.placeholder || null, value: i.value}))
    };
}
"""


async def achar_aba_gc(browser):
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if GC_MARKER in (pg.url or "").lower():
                return ctx, pg
    raise SystemExit(
        "Nenhuma aba da GC encontrada no Chrome CDP (%s).\n"
        "Rode '2 - ABRIR CHROME.bat', faca login na GC e deixe a aba aberta." % CDP_URL
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eu-confirmo", action="store_true",
                    help="habilita fases B/C (interagem com o form, mas NAO salvam)")
    ap.add_argument("--cnpj-ok", default="", help="CNPJ de sacado JA cadastrado na GC")
    ap.add_argument("--cnpj-novo", default="", help="CNPJ valido NAO cadastrado na GC")
    args = ap.parse_args()

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            raise SystemExit("Nao consegui conectar no Chrome CDP em %s: %s" % (CDP_URL, str(e)[:200]))

        ctx, page = await achar_aba_gc(browser)

        # Captura dialogos nativos (window.confirm/alert). Se a GC usar isso,
        # o Playwright auto-dismissa e o Salvar seria cancelado em silencio.
        def _on_dialog(d):
            RESULTADO["dialogos_nativos"].append({"type": d.type, "message": d.message})
            print("  [DIALOGO NATIVO] %s: %s" % (d.type, d.message))
            asyncio.ensure_future(d.dismiss())

        page.on("dialog", _on_dialog)

        try:
            etapa("A0_contexto", {
                "url": page.url,
                "title": await page.title(),
                "body_class": await page.evaluate("() => document.body.className"),
                "logado": "login" not in (page.url or "").lower(),
            })
            if "login" in (page.url or "").lower():
                raise SystemExit("A aba da GC esta na tela de LOGIN. Faca login manualmente e rode de novo.")

            # A1 - navegacao pra digitacao
            via_link = await page.evaluate("""() => {
                const a = document.querySelector('a[href*="/operacao/digitacao"]');
                if (!a) return {achou: false, href: null};
                const href = a.getAttribute('href');
                a.click();
                return {achou: true, href: href};
            }""")
            await page.wait_for_timeout(3000)
            url_pos_link = page.url
            tem_novo = await page.evaluate(
                """() => [...document.querySelectorAll('button')]
                   .some(b => b.offsetParent && b.textContent.trim() === 'Novo')"""
            )

            goto_direto = None
            if not tem_novo:
                # A GC e a unica das 3 sem fallback de URL direta - testa se funcionaria
                try:
                    await page.goto(GC_URL + "/operacao/digitacao",
                                    wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(2500)
                    goto_direto = {
                        "url_final": page.url,
                        "tem_botao_novo": await page.evaluate(
                            """() => [...document.querySelectorAll('button')]
                               .some(b => b.offsetParent && b.textContent.trim() === 'Novo')"""),
                    }
                except Exception as e:
                    goto_direto = {"erro": str(e)[:200]}

            etapa("A1_navegacao_digitacao", {
                "link_menu": via_link,
                "url_pos_click": url_pos_link,
                "tem_botao_novo_apos_link": tem_novo,
                "fallback_goto_direto": goto_direto,
            })

            # A2 - vocabulario do grid (antes de abrir modal)
            try:
                await _clicar_seguro(page, "Pesquisar", "listagem de operacoes")
                await page.wait_for_timeout(2500)
            except SystemExit:
                raise
            except Exception:
                pass
            etapa("A2_grid_listagem", await page.evaluate(JS_GRID_STATUS))

            # A3 - abre "Novo" e inspeciona modal/abas
            clicou_novo = await _clicar_seguro(page, "Novo", "abrir formulario")
            await page.wait_for_timeout(2500)
            dados_modal = await page.evaluate(JS_MODAIS_ABAS)
            dados_modal["clicou_novo"] = clicou_novo
            etapa("A3_modal_apos_novo", dados_modal)

            # A4 - ativa aba Digitacao e dumpa os 9 IDs
            ativou = await page.evaluate("""() => {
                for (const li of document.querySelectorAll('.aba-cabecalho-lista-li')) {
                    const t = (li.textContent || '').trim();
                    if ((t === 'Digitação' || t === 'Digitacao') && li.offsetParent) {
                        li.click(); return t;
                    }
                }
                return null;
            }""")
            await page.wait_for_timeout(2000)
            campos = await page.evaluate(JS_DUMP_CAMPOS, IDS_ALVO)
            faltando = [k for k, v in campos.items() if v == "NAO_EXISTE"]
            invisiveis = [k for k, v in campos.items()
                          if isinstance(v, dict) and not v.get("visivel")]
            etapa("A4_aba_digitacao", {
                "aba_ativada": ativou,
                "campos_alvo": campos,
                "RESUMO_ids_faltando": faltando,
                "RESUMO_ids_invisiveis": invisiveis,
                "VEREDITO": ("FORM COMPATIVEL COM A FIRMA" if not faltando
                             else "DIVERGENTE - faltam: %s" % faltando),
            })
            etapa("A4b_inventario_completo", {"campos": await page.evaluate(JS_INVENTARIO)})

            # A4c - botoes por nivel (valida estrategia do Salvar)
            ancora = "valo_titu" if campos.get("valo_titu") != "NAO_EXISTE" else "nume_nota"
            etapa("A4c_botoes_por_nivel", await page.evaluate(JS_BOTOES_NIVEL, ancora))

            # A5 - linhas/acoes (existe Excluir?)
            await page.evaluate("""() => {
                for (const li of document.querySelectorAll('.aba-cabecalho-lista-li')) {
                    const t = (li.textContent || '').trim();
                    if ((t === 'Operação' || t === 'Operacao') && li.offsetParent) { li.click(); return; }
                }
            }""")
            await page.wait_for_timeout(2000)
            etapa("A5_linhas_acoes", {"linhas": await page.evaluate(JS_LINHAS_ACOES)})

            # FASES B/C
            if not args.eu_confirmo:
                etapa("B_C_puladas", {
                    "motivo": "rode com --eu-confirmo --cnpj-ok <CNPJ> --cnpj-novo <CNPJ> "
                              "para medir latencia do lookup e testar mascaras (nao salva nada)"})
            else:
                await page.evaluate("""() => {
                    for (const li of document.querySelectorAll('.aba-cabecalho-lista-li')) {
                        const t = (li.textContent || '').trim();
                        if ((t === 'Digitação' || t === 'Digitacao') && li.offsetParent) { li.click(); return; }
                    }
                }""")
                await page.wait_for_timeout(1500)

                # B1 - latencia do lookup com CNPJ conhecido
                if args.cnpj_ok:
                    cnpj = re.sub(r"\D", "", args.cnpj_ok)
                    t0 = time.time()
                    await page.fill("#saca_id", cnpj)
                    await page.press("#saca_id", "Tab")
                    popup_ms, nome_ms, nome_val = None, None, ""
                    for _ in range(200):  # 200 x 100ms = 20s
                        await page.wait_for_timeout(100)
                        st = await page.evaluate("""() => {
                            const n = document.getElementById('saca_nome');
                            const nome = n ? (n.value || '') : '';
                            const pop = [...document.querySelectorAll('*')].some(e =>
                                e.offsetParent && /cadastro de sacado/i.test(e.textContent || '')
                                && (e.textContent || '').length < 120);
                            return {nome: nome, popup: pop};
                        }""")
                        if st["popup"] and popup_ms is None:
                            popup_ms = int((time.time() - t0) * 1000)
                        if st["nome"] and len(st["nome"].strip()) > 1 and nome_ms is None:
                            nome_ms = int((time.time() - t0) * 1000)
                            nome_val = st["nome"]
                            break
                    etapa("B1_lookup_cnpj_conhecido", {
                        "cnpj": cnpj,
                        "ms_ate_nome_preencher": nome_ms,
                        "nome_encontrado": nome_val,
                        "ms_ate_popup_cadastro": popup_ms,
                        "timeout_20s": nome_ms is None and popup_ms is None,
                        "BUDGET_FIRMA_2500ms_SUFICIENTE": (nome_ms or 99999) <= 2500,
                        "BUDGET_FLUX_5300ms_SUFICIENTE": (nome_ms or 99999) <= 5300,
                    })

                # C1 - mascaras: preenche e RELE (nunca salva)
                testes = [
                    ("#data_titu", "31/12/2099"), ("#valo_titu", "1234,56"),
                    ("#nume_doct", "999999"), ("#nume_nota", "999999"),
                    ("#data_emis", "01/01/2099"), ("#valo_nota", "1234,56"),
                    ("#chave_nf", "1" * 44),
                ]
                leituras = []
                for sel, val in testes:
                    try:
                        await page.fill(sel, val, timeout=4000)
                        await page.wait_for_timeout(150)
                        lido = await page.input_value(sel, timeout=3000)
                        d_dig = re.sub(r"\D", "", val or "")
                        d_lido = re.sub(r"\D", "", lido or "")
                        leituras.append({
                            "campo": sel, "digitado": val, "lido": lido,
                            "igual_literal": lido == val,
                            "igual_normalizado": d_lido == d_dig,
                            "ALERTA_MASCARA": d_lido != d_dig,
                        })
                    except Exception as e:
                        leituras.append({"campo": sel, "erro": str(e)[:150]})
                alertas = [l for l in leituras if l.get("ALERTA_MASCARA")]
                etapa("C1_mascaras_dry_fill", {
                    "leituras": leituras,
                    "RESUMO": ("todas as mascaras aceitaram o formato da Firma"
                               if not alertas else "ATENCAO - mascara deturpou: %s" % alertas),
                })

                # B2 - popup de cadastro com CNPJ desconhecido
                if args.cnpj_novo:
                    cnpj_n = re.sub(r"\D", "", args.cnpj_novo)
                    await page.fill("#saca_id", "")
                    await page.fill("#saca_id", cnpj_n)
                    await page.press("#saca_id", "Tab")
                    await page.wait_for_timeout(6000)
                    pop = await page.evaluate("""() => {
                        const ids = ['iden','nome','cep','ende','nume','bair','cida','uf','e_mail'];
                        const out = {campos: {}, titulos_modais: [], botoes: []};
                        for (const id of ids) {
                            const el = document.getElementById(id);
                            out.campos[id] = el ? {visivel: el.offsetParent !== null,
                                                   maxlength: el.getAttribute('maxlength'),
                                                   required: !!el.required} : "NAO_EXISTE";
                        }
                        for (const m of document.querySelectorAll('.modal-interna-fundo')) {
                            if (!m.offsetParent) continue;
                            const tm = m.querySelector('.modal-titulo');
                            out.titulos_modais.push(tm ? (tm.textContent || '').trim() : '');
                            for (const b of m.querySelectorAll('button')) {
                                if (b.offsetParent) out.botoes.push((b.textContent || '').trim());
                            }
                        }
                        return out;
                    }""")
                    etapa("B2_popup_cadastro_sacado", pop)
                    await page.keyboard.press("Escape")  # fecha SEM salvar
                    await page.wait_for_timeout(800)

            try:
                OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(OUT_PNG))
            except Exception:
                pass

            # Fecha o modal com Escape - nada foi salvo
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

        except SystemExit:
            raise
        except Exception as e:
            import traceback
            RESULTADO["erro"] = {"msg": str(e)[:400], "traceback": traceback.format_exc()[-1500:]}
            print("\n!! ERRO: %s" % e)

        etapa("Z_dialogos_nativos", {
            "capturados": RESULTADO["dialogos_nativos"],
            "NOTA": ("nenhum dialogo nativo - bom sinal, a confirmacao deve ser DOM"
                     if not RESULTADO["dialogos_nativos"]
                     else "ATENCAO: a GC usa dialogo NATIVO. O Playwright auto-dismissa "
                          "e o Salvar seria cancelado em silencio - precisa de handler."),
        })

        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(RESULTADO, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n>>> Dump salvo em: %s" % OUT_JSON)
        print(">>> Nada foi salvo no portal da GC. Sua sessao continua ativa.")


if __name__ == "__main__":
    asyncio.run(main())
