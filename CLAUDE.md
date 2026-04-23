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

Para manter o servidor rodando sem travar o terminal (após fechar o shell), usar:
```bash
nohup .venv/Scripts/python.exe backend/main.py >> server_out.log 2>> server_err.log &
```

---

## Estrutura de arquivos

```
backend/
  main.py                          # FastAPI — endpoints e orquestração background
  config_manager.py                # Credenciais encriptadas em ~/.automacao_factory/
  historico_manager.py             # Persiste operações em operacoes_historico.json
  factory_manager.py               # Gerencia factories extras (CRUD em JSON)
  services/
    excel_processor.py             # Login GW → baixa 2 relatórios Excel → parse faturas
    firma_automation.py            # Playwright → Firma Capital (headless=True)
    fluxasset_automation.py        # Playwright → FluxAsset (headless=False, channel="chrome" — Cloudflare)
    gc_automation.py               # Playwright → GC Recursos (headless=True)
    documentos.py                  # Playwright → GW → baixa boletos PDF + CTes PDF/ZIP

frontend/
  index.html                       # SPA completa (tema escuro tech, sidebar PatternFly-style)
```

---

## Fluxo de status da operação

```
iniciando → executando → salvando_documentos → concluido
                                             ↘ concluido_com_erros
```

O frontend faz polling em `/api/status/{op_id}` a cada 1,5s e **para apenas em `concluido` ou `concluido_com_erros`**. O status `salvando_documentos` mantém o polling ativo (já tratado no `renderStatus` do frontend).

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
# Aguardar URL mudar (não hardcode "/home" — pode redirecionar diferente)
await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
```
⚠️ O botão de login tem classe `button-login` sem `type="submit"`. Seletores genéricos (`button[type="submit"]`) não funcionam.

### Navegação — sempre por URL direta (nunca clicar em menus)
O GW usa iframes e carregamento dinâmico. Clicar em "Lançamentos → Financeiro → ..." não funciona em headless.

Usar `wait_until="domcontentloaded"` (nunca `networkidle` — o GW tem requests contínuos em background que nunca param).

---

## DOWNLOAD DE DOCUMENTOS — Fluxo Correto (observado manualmente)

### Faturas / Boletos PDF

**Página:** `/consultafatura?acao=iniciar`

**Fluxo correto:**
1. Navegar para `/consultafatura?acao=iniciar`
2. Filtro: `select[name="campoDeConsulta"]` → `emissao_fatura`
3. Datas: `input[name="dtemissao1"]` e `input[name="dtemissao2"]` → **data de hoje** no formato `DD/MM/AAAA`
4. Filial: `select[name="filialId"]` → `1`=MATRIZ, `2`=Filial SP
5. Clicar `input[value="Pesquisar"]`
6. Selecionar checkboxes das faturas desejadas (two-pass para evitar stale ElementHandle)
7. Selecionar **"Modelo 10"** no select "Modelo de impressão em PDF" (encontrado dinamicamente — select que tem opções com texto "Modelo")
8. Clicar no **ícone PDF vermelho** ao lado do dropdown Modelo 10
   - Esse ícone é uma `img` ou `a` com onclick contendo "relatorio"/"gerar"
   - ⚠️ NÃO é o botão "Imprimir Boletos" — esse gera via BoletoServlet (formato diferente)
9. O GW gera o PDF **assincronamente via S3**: `gw-saas-relatorios.s3.us-east-2.amazonaws.com/gerados/SERTANEJO/faturamod10_<uuid>.pdf`
10. Capturar via `context.on("request", handler)` interceptando URLs do S3

**PDF gerado:** Um único PDF com todas as faturas selecionadas
**Nome do arquivo salvo:** `Boleto - {Factory} - {DD-MM-AAAA}.pdf`

**Resultado reportado no status:**
```python
rd["fatura_pdf"] = {"ok": True, "arquivo": nome_arquivo, "qtd": marcadas}
# ⚠️ Campo é "fatura_pdf", NÃO "boleto"
```

**⚠️ NÃO USAR:** `BoletoServlet` / botão "Imprimir Boletos" — gera boleto em formato diferente

---

### CTes PDF

**Página:** `/CTeControlador?acao=listar`  ← **DIFERENTE de `/consultaconhecimento`**

**Fluxo correto:**
1. Navegar para `/CTeControlador?acao=listar` (`wait_until="load"`)
2. Setar campo de busca para **"Número Fatura"** (`#campo_consulta`)
3. Aguardar `#valor_consulta` e `#valor_consulta2` ficarem visíveis
4. Preencher número da fatura (6 dígitos com zeros, ex: "005028") em `#valor_consulta`
5. Preencher ano (ex: "2026") em `#valor_consulta2`
6. Configurar `#statusCte` → `""` (todos), `#tipoTransporte` → `""`, `#limite` → `"200"`, `#filial`
7. **Chamar `consulta()` diretamente via JS** (`page.evaluate("consulta()")`) em vez de clicar `#pesquisar`
   - ⚠️ `#pesquisar` chama `tryRequestToServer()` que verifica `session_test.jsp` → retorna vazio em contexto automatizado → cancela a busca
8. Aguardar resultado atualizar via `wait_for_function` que checa mudança no texto de ocorrências
9. Se total > 0: marcar `#ckTodos`, clicar `#img_imprimir`, capturar PDF via S3

**PDF gerado:** Um único PDF com TODOS os CTes da fatura agrupados
**Nome do arquivo salvo:** `CTe - Fatura {numero}.pdf` (sem nome da factory)

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

Para o CTeControlador, usar número com zeros à esquerda (6 dígitos) ao preencher o campo.

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

### GC Recursos — fluxo de digitação

**Login GW (dentro do gc_automation):** usa o mesmo padrão de `documentos.py`:
```python
await page.locator('button.button-login').click()
await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
```

**Navegação GC:** tentar URL direta `/operacao/digitacao` primeiro; fallback via menu.

**Campos críticos:**
- Número de nota: `input[placeholder*="m.Nota"]` ou `input[placeholder*="Nota"]`
- Operação: `text=Operação` nos labels do formulário

**Finalizaçao:** SEMPRE manual (igual a Firma e FluxAsset). Após digitar todos os títulos, o usuário acessa o site manualmente para definir conta corrente e encaminhar.

---

## Factories Extras (personalizadas)

O usuário pode cadastrar factories adicionais além das 6 padrão.

**Endpoint:** `POST /api/factories-extras`
**Campos:** `nome`, `icone`, `url`, `usuario`, `senha`
**Armazenamento:** `factory_manager.py` → persiste em JSON local

As credenciais de factories extras são salvas no mesmo objeto da factory (não no `config_manager`).

---

## Histórico e relatórios — detalhes importantes

### Rastreamento de faturas por factory

O `historico_manager.py` usa `status["factories"]` diretamente (não `factory_sugerida`):
```python
for sistema, fs in status.get("factories", {}).items():
    fat_salvas = fs.get("faturas_salvas", set())
    ...
```

⚠️ O `excel_processor.py` tem campo `factory_sugerida` hardcoded como `firma_*` — NÃO usar para relatórios. Sempre usar o sub-status por factory em `status["factories"]`.

### Tempo de operação

O `inicio` da operação é enviado pelo frontend como o timestamp de quando o usuário clicou "Carregar do GW" (não quando clicou "Executar"):
```js
// No executarAutomacao():
inicio: operacaoStartTime ? new Date(operacaoStartTime).toISOString() : null
```
Isso garante que o tempo total inclui o carregamento do Excel.

### Campo fatura_pdf no resumo de documentos

O backend salva em `rd["fatura_pdf"]` (não `rd["boleto"]`). O frontend deve ler `info.fatura_pdf`:
```js
const b = info.fatura_pdf;  // ← correto
// const b = info.boleto;   // ← ERRADO
```

---

## Frontend — design e layout

### Tema escuro tech (desde 22/04/2026)

**CSS Variables:**
```css
:root {
  --bg: #0B0F1A; --surface: #111827; --surface2: #1A2234;
  --border: #1F2D40; --border2: #2A3A50;
  --accent: #4F8EF7; --accent-h: #3B7EF0; --accent-s: rgba(79,142,247,0.12);
  --green: #10B981; --green-s: rgba(16,185,129,0.12);
  --text: #E2E8F0; --text2: #94A3B8; --muted: #64748B;
}
```

### Layout sidebar (PatternFly-style)

```
.app-shell (flex column, 100vh)
  ├── .topbar (56px, fundo #070C15)
  └── .layout (flex row, restante)
        ├── .sidebar (220px, fundo #0D1424)
        └── main (flex:1, overflow-y:auto)
```

```css
body { height: 100vh; overflow: hidden; }
.app-shell { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
.layout { display: flex; flex: 1; height: calc(100vh - 56px); overflow: hidden; }
.sidebar { width: 220px; }
main { flex: 1; overflow-y: auto; overflow-x: hidden; }
```

### KPI grid

2 linhas × 3 colunas:
```css
.kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
```

Cards com barra lateral colorida, gradiente e animação `cardGlowPulse`.
Valores com `clamp(22px, 2.2vw, 32px)` + `white-space: nowrap; overflow: hidden; text-overflow: ellipsis` para evitar quebra de linha em valores monetários grandes.

### Regra crítica de CSS

**NÃO colocar `width: 100%` em seletores globais `input, select`** — quebra os selects da toolbar de fatura:
```css
/* ERRADO: */
input, select { width: 100%; }

/* CORRETO: escopar apenas dentro de .field */
.field input, .field select { width: 100%; }
```

### Logo

Lightning bolt sólido (preenchido):
```html
<svg fill="currentColor" stroke="none" ...>
  <path d="M13 2L4.5 13.5H11L10 22L19.5 10.5H13L13 2Z"/>
</svg>
```

### Animações

```css
@keyframes fadeSlideIn { from { opacity:0; transform:translateY(12px); } to { opacity:1; } }
@keyframes cardGlowPulse { 0%,100% { box-shadow: 0 0 0 1px var(--border), 0 4px 20px rgba(0,0,0,.4); }
  50% { box-shadow: 0 0 0 1px rgba(79,142,247,.3), 0 4px 24px rgba(79,142,247,.08); } }
.page.active { animation: fadeSlideIn .35s ease both; }
.kpi-card { animation: cardGlowPulse 4s ease-in-out infinite; }
.kpi-card:hover { transform: translateY(-3px); animation: none; }
```

---

## Pasta de destino e documentos

- Usuário seleciona a pasta antes de executar; o backend abre o seletor nativo via tkinter (`/api/selecionar-pasta`)
- Se `pasta_destino` for informada no request, o salvamento ocorre automaticamente ao final da digitação
- Estrutura salva:
  - `Boleto - {Factory} - {DD-MM-AAAA}.pdf` — PDF de faturas agrupadas (Modelo 10)
  - `CTe - Fatura {numero}.pdf` — PDF de todos CTes de uma fatura (sem nome da factory no arquivo)
  - `CTEs - {Factory} - {DD-MM-AAAA}.zip` — todos os CTes da factory zipados

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
| Servidor morria ao fechar shell | Processo filho do terminal | `nohup ... &` |
| Fatura PDF mostrando "—" no resumo | Frontend lia `info.boleto`, backend salva `info.fatura_pdf` | Corrigir field name no frontend |
| FluxAsset ausente no "Uso por factory" | `factory_sugerida` hardcoded como `firma_*` no excel | `historico_manager` usa `status["factories"]` diretamente |
| Duração não incluía carregamento GW | `inicio` era timestamp do clique em "Executar" | Frontend envia `operacaoStartTime` (clique em "Carregar do GW") |
| KPI value quebrando em 3 linhas | Font size fixo + wrap | `clamp()` + `white-space: nowrap; overflow: hidden; text-overflow: ellipsis` |
| Toolbar quebrada (selects largura 100%) | CSS global `input, select { width:100% }` | Escopar apenas para `.field input, .field select` |
| Encoding corrompido no gc_automation | Arquivo salvo sem UTF-8 correto | Reescrever arquivo inteiro com encoding correto |
| CTe busca retornava 0 ocorrências | `#pesquisar` chama `tryRequestToServer()` → falha sem sessão real | Chamar `consulta()` diretamente via `page.evaluate()` |
| GC remessa URL 404 | URL `/gerarremessa?acao=iniciar` não existe no GW | URL real é `/jspexporta_boleto.jsp` (descoberta via inspeção de `li[href]` do menu) |
| GC login timeout | Selector `button[type="submit"], input[type="submit"]` de código antigo | Usar `#btnEntrar` (id específico do botão) |
| GC pagination 10 resultados | `select[name="limiteResultados"]` default=10 | Selecionar `value="200"` antes de clicar Pesquisar (documentos.py) |

---

## GC Recursos — Remessa GW (descoberto em 22/04/2026)

A URL `/gerarremessa?acao=iniciar` NÃO existe — retorna 404.
O menu do GW usa `li[href="./jspexporta_boleto.jsp"]` para navegar.

**URL real:** `/jspexporta_boleto.jsp`

**Campos confirmados via inspeção:**
```
select[name="campoDeConsulta"]  → "Data de Emissão" (value=emissao_fatura)
input[name="dtemissao1"]        → data inicial (pré-preenchido com hoje)
input[name="dtemissao2"]        → data final
select[name="conta"]            → conta bancária (label "03196-8 / BRADESCO" para GC)
select[name="tipoGerado"]       → "naoGerado" para filtrar apenas não exportados
input[name="pesquisar"]         → botão Pesquisar (type=button)
input[value="Exportar Boletos"] → botão de download (type=button)
```

**Estrutura da tabela de resultados:**
- Col 0 = checkbox (`input[type="checkbox"]`)
- Col 1 = Fatura (número como "005148" ou "005148/2026")
- Col 2 = Nosso Número, Col 3 = Emissão, Col 4 = Cliente...

**Contas por sistema:**
- `gc_matriz` = "3196-8" → `select_option` por `o.text.includes('3196-8')`
- `gc_sp` = "03196-8" → mesmo

**Login GC Recursos:**
- `input#Email` = usuário
- `input#Password` = senha
- `button#btnEntrar` = botão login (tem `g-recaptcha` class mas sitekey vazio)
- Login confirmado funcionando via teste automatizado

---

## Estado atual (22/04/2026)

**Testado e funcionando:**
- Firma Capital (Matriz e SP)
- FluxAsset (Matriz e SP)
- Download de boletos (fatura PDF) via GW
- Download de CTes via GW
- Relatório: fatura PDF count, uso por factory, duração total
- GC login (`#btnEntrar`) — confirmado via teste
- `limiteResultados=200` na busca de faturas GW (documentos.py)

**Pendente de teste em produção:**
- GC Recursos fluxo completo (remessa GW + import GC + Núm.Nota)
  - URL da remessa corrigida para `/jspexporta_boleto.jsp`
  - Login GC corrigido para `#Email`, `#Password`, `#btnEntrar`
  - Seletores da tabela de resultados confirmados via inspeção
