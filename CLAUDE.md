# AutoFactory — Contexto do Projeto

## O que é

Ferramenta de automação para operações de factoring de uma transportadora.
Fluxo completo: carrega faturas do GW Webtrans → usuário atribui cada fatura a uma factory → sistema digita automaticamente nos portais das factories → baixa boletos e CTes do GW e salva na pasta escolhida.

## Como rodar

```
Executável: C:\Users\Samsung\Desktop\automacao-factory\iniciar.bat
```

O `.bat` abre `frontend/index.html` no navegador e inicia o backend com:
```
C:\Claude Operações\automacao-factory\.venv\Scripts\python.exe backend\main.py
```

Backend roda em `http://localhost:8000`. Todo o código-fonte de referência fica em:
```
C:\Claude Operações\automacao-factory\
```
Uma cópia espelho existe em `C:\Users\Samsung\Desktop\automacao-factory\` — sempre que editar, copiar para os dois lugares e reiniciar o servidor.

---

## Estrutura de arquivos

```
backend/
  main.py                          # FastAPI — endpoints e orquestração background
  config_manager.py                # Credenciais encriptadas em ~/.automacao_factory/
  historico_manager.py             # Persiste operações em operacoes_historico.json
  services/
    excel_processor.py             # Login GW → baixa 2 relatórios Excel → parse faturas
    firma_automation.py            # Playwright → Firma Capital (headless=True)
    fluxasset_automation.py        # Playwright → FluxAsset (headless=False, channel="chrome" — Cloudflare)
    gc_automation.py               # Playwright → GC Recursos (headless=True)
    documentos.py                  # Playwright → GW → baixa boletos PDF + CTes PDF/ZIP

frontend/
  index.html                       # SPA com fetch para o backend; tema claro, Inter font
```

---

## Fluxo de status da operação

```
iniciando → executando → salvando_documentos → concluido
                                             ↘ erro
```

O frontend faz polling em `/api/status/{op_id}` a cada 1,5s e **para apenas em `concluido` ou `erro`**. O status `salvando_documentos` mantém o polling ativo (já tratado no `renderStatus` do frontend).

Credenciais salvas por sistema: `gw`, `firma_matriz`, `firma_sp`, `fluxasset_matriz`, `fluxasset_sp`, `gc_matriz`, `gc_sp`.

---

## GW Webtrans — detalhes críticos descobertos por teste

**Base URL:** `https://webtrans.saas.gwsistemas.com.br`

### Login
```python
await page.goto(f"{BASE_GW}/login", wait_until="domcontentloaded")
await page.locator('input[name="login"]').fill(usuario)
await page.locator('input[name="senha"]').fill(senha)
await page.locator('button.button-login').click()   # ← NÃO tem type="submit"
await page.wait_for_url(f"{BASE_GW}/home", timeout=15000)
```
⚠️ O botão de login tem classe `button-login` sem `type="submit"`. Seletores genéricos (`button[type="submit"]`) não funcionam.

### Navegação — sempre por URL direta (nunca clicar em menus)
O GW usa iframes e carregamento dinâmico. Clicar em "Lançamentos → Financeiro → ..." não funciona em headless.

---

## DOWNLOAD DE DOCUMENTOS — Fluxo Correto (observado manualmente)

### ⚠️ ATENÇÃO — O fluxo de documentos foi completamente reescrito após observação do usuário em 18/04/2026

O código anterior estava usando páginas e botões ERRADOS. O fluxo real observado é:

---

### Faturas / Boletos PDF

**Página:** `/consultafatura?acao=iniciar`

**Fluxo correto:**
1. Navegar para `/consultafatura?acao=iniciar`
2. Filtro: `select[name="campoDeConsulta"]` → `emissao_fatura`
3. Datas: `input[name="dtemissao1"]` e `input[name="dtemissao2"]` → **data de hoje** no formato `DD/MM/AAAA`
4. Filial: `select[name="filialId"]` → `1`=MATRIZ, `2`=Filial SP
5. Clicar `input[value="Pesquisar"]`
6. Selecionar checkboxes das faturas desejadas
7. Selecionar **"Modelo 10"** no select "Modelo de impressão em PDF" (encontrado dinamicamente — select que tem opções com texto "Modelo")
8. Clicar no **ícone PDF vermelho** ao lado do dropdown Modelo 10
   - Esse ícone é uma `img` ou `a` com onclick contendo "relatorio"/"gerar"
   - ⚠️ NÃO é o botão "Imprimir Boletos" — esse gera via BoletoServlet (formato diferente)
9. O GW gera o PDF **assincronamente via S3**: `gw-saas-relatorios.s3.us-east-2.amazonaws.com/gerados/SERTANEJO/faturamod10_<uuid>.pdf`
10. Capturar via `context.on("request", handler)` interceptando URLs do S3

**PDF gerado:** Um único PDF com todas as faturas selecionadas (ex: 4 páginas para 3 faturas)
**Nome do arquivo S3:** `faturamod10_<uuid>.pdf`

**⚠️ NÃO USAR:** `BoletoServlet` / botão "Imprimir Boletos" — gera boleto em formato diferente do que o usuário usa

---

### CTes PDF

**Página:** `/CTeControlador?acao=listar`  ← **DIFERENTE de `/consultaconhecimento`**

**Fluxo correto:**
1. Navegar para `/CTeControlador?acao=listar`
2. Setar campo de busca para **"Número Fatura"** (primeiro select que tem essa opção)
3. Preencher **número** da fatura (sem zeros à esquerda, ex: "5028" para fatura "005028")
4. Preencher **ano** (ex: "2026")
5. Setar **filial**: label "MATRIZ" ou "Filial SP" (opções do select que contém essas labels)
6. Clicar `input[value="Pesquisar"]`
7. Extrair os **IDs dos CTes** dos checkboxes da página de resultados (valores numéricos ≥ 4 dígitos)
8. **Navegar diretamente para a URL de exportação:**
   ```
   /redireciona_relatorio.jsp?url=./listar_cte.jsp?acao=exportar&modelo=17&idCte=ID1,ID2,ID3,...
   ```
9. O GW gera o PDF assincronamente via S3: `gw-saas-relatorios.s3.us-east-2.amazonaws.com/gerados/SERTANEJO/dacte_mod17_<uuid>.pdf`
10. Capturar via `context.on("request", handler)` interceptando URLs do S3

**PDF gerado:** Um único PDF com TODOS os CTes da fatura agrupados (ex: 16 páginas para 1 fatura com muitos CTes)
**Nome do arquivo S3:** `dacte_mod17_<uuid>.pdf`

**⚠️ NÃO USAR:** `/consultaconhecimento?acao=iniciar` — essa página existe mas NÃO é a que o usuário usa para baixar CTes

---

### Filial no CTeControlador vs consultafatura

| Sistema | consultafatura (filialId) | CTeControlador (label) |
|---|---|---|
| firma_matriz / fluxasset_matriz / gc_matriz | "1" | "MATRIZ" |
| firma_sp / fluxasset_sp / gc_sp | "2" | "Filial SP" |

---

### Padrão S3 (ambos boleto e CTe)

```python
# Capturar URL S3 via context listener
def capturar_s3(request):
    u = request.url
    if "gw-saas-relatorios.s3" in u and "gerados" in u:
        s3_holder["url"] = u

context.on("request", capturar_s3)
# Aguardar até 60s (40 tentativas × 1.5s)
# Baixar com context.request.get(s3_holder["url"])
```

---

### Números de fatura — normalização de zeros

O excel_processor pode retornar "5028" (sem zeros) mas o GW pode mostrar "005028".
Usar `_normalizar()` para comparar: `str(num).lstrip("0") or "0"`

Para o CTeControlador, testar busca com número sem zeros (`_normalizar(numero)`).

---

## Factories — URLs e automação

| Sistema | URL base | headless |
|---|---|---|
| Firma Capital | `https://intrafac777.firmasa.com/Factadebentures` | True |
| FluxAsset | `https://portal.fluxasset.com.br/Factaconsult` | **False** (Cloudflare Turnstile) |
| GC Recursos | `http://gcrecursos.dyndns.org:9000/FactaConsult` | True |

**Links no box de conclusão do frontend:**
- Firma: `.../Factadebentures/login`
- FluxAsset: `.../Factaconsult/login`
- GC: `.../FactaConsult/login`

**Mapeamento filial GW por factory:**
```python
_FILIAL_ID = {
    "firma_matriz": "1", "firma_sp": "2",
    "fluxasset_matriz": "1", "fluxasset_sp": "2",
    "gc_matriz": "1", "gc_sp": "2",
}
```

---

## Pasta de destino e documentos

- Usuário seleciona a pasta antes de executar; o backend abre o seletor nativo via tkinter (`/api/selecionar-pasta`)
- Se `pasta_destino` for informada no request, o salvamento ocorre automaticamente ao final da digitação
- Estrutura salva:
  - `Boleto - {Factory} - {DD-MM-AAAA}.pdf` — PDF de faturas agrupadas (Modelo 10)
  - `CTe - {Factory} - Fatura {numero}.pdf` — PDF de todos CTes de uma fatura agrupados
  - `CTEs - {Factory} - {DD-MM-AAAA}.zip` — todos os CTes da factory zipados

---

## Frontend — features implementadas

- Timer: começa ao clicar "Carregar do GW", exibe tempo total na caixa de conclusão
- Caixa de conclusão mostra:
  - Resumo de digitação (factory / faturas / valor)
  - Resumo de documentos (Boleto PDF / CTes PDF / ZIP por factory)
  - Tempo total decorrido
  - Links para abrir cada factory

---

## Dependências Python

```
fastapi, uvicorn, playwright, pandas, openpyxl, httpx, cryptography, pydantic
```
Playwright usa Chromium instalado via `playwright install chromium`.

---

## Problemas já resolvidos (não reinventar)

| Problema | Causa | Solução |
|---|---|---|
| Login GW não funcionava | Botão sem `type="submit"` | `button.button-login` |
| Navegação por menu falhava | GW usa iframes | Navegar por URL direta |
| PDF de boleto não baixava | Usava BoletoServlet — fluxo errado | Usar ícone PDF vermelho (Modelo 10) → S3 |
| PDF de CTe não baixava | Usava `/consultaconhecimento` — página errada | Usar `/CTeControlador?acao=listar` |
| Input pesquisar não encontrado | `name=" pesquisar "` tem espaço | `input[value="Pesquisar"]` |
| Linhas erradas na tabela | Múltiplas tables no DOM | Filtrar por regex `\d{5,6}/\d{4}` |
| `showDirectoryPicker` retornava só o nome | API browser não dá path completo | tkinter `filedialog.askdirectory` no backend |
| Frontend parava de atualizar durante download | Polling parava em `concluido` | Status intermediário `salvando_documentos` |
| PDF com 403 | `httpx` sem cookies | `context.request.get()` (compartilha sessão) |
| "0 in '10'" bug | `"0" in texto` = True para "10" | `re.search(r':\s*(\d+)')` + comparar `== 0` |
| Zeros à esquerda no nº fatura | GW mostra "005028", excel retorna "5028" | `_normalizar()` strip leading zeros ambos lados |
| Segundo trigger lançava exceção | `await trigger_fn()` no except propagava erro | Envolver em try/except pass |

---

## Estado atual do código (18/04/2026)

O `documentos.py` foi **completamente reescrito** com o fluxo correto observado manualmente.
Ainda não testado em produção. Próximo passo: testar e ajustar seletores do formulário CTeControlador (campos preenchidos via JavaScript dinâmico — pode precisar de ajuste fino nos nomes dos inputs).

O código usa logs de diagnóstico extensos para facilitar debug:
- Mostra todos selects e inputs encontrados na página
- Mostra resultado de cada passo do preenchimento
- Mostra IDs de CTes capturados
