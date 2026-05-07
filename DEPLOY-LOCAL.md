# Distribuição local do AutoFactory

Como distribuir o AutoFactory para o computador dos liderados sem que cada um precise saber Git, Python ou ambiente virtual.

---

## Visão geral

```
Você (admin)              Liderado
────────────              ────────
1. Mantém o repo no       1. Recebe 4 arquivos (~10 KB)
   GitHub                 2. Coloca numa pasta no PC
2. Faz commits +          3. Clica 2x no instalar.bat (1 vez)
   push como sempre       4. Usa iniciar.bat dia a dia
3. Avisa: "tem            5. Quando admin avisa: clica
   atualização"               atualizar.bat
```

---

## Pré-requisitos do repositório

**O repo precisa estar PÚBLICO** (ou os instaladores precisam ter um token embutido — não recomendado).

### Como tornar o repo público

1. https://github.com/jonathasjga-cpu/automacao-factory/settings
2. Role até "Danger Zone" → "Change repository visibility"
3. "Change to public"

### Por que público

Os scripts `instalar-autofactory.bat` e `atualizar-autofactory.bat` baixam o ZIP da branch `main` via:
```
https://github.com/jonathasjga-cpu/automacao-factory/archive/refs/heads/main.zip
```

Esta URL só funciona sem autenticação se o repo for **público**.

### Alternativa se quiser manter privado

Cada liderado precisaria ter uma conta GitHub com acesso ao repo + um Personal Access Token. Os scripts incluiriam o token. Não recomendo: token hard-coded no `.bat` é risco de segurança e dificulta gerenciamento.

---

## O que distribuir pros liderados

Manda **4 arquivos** (do diretório `scripts-instalador/`):

```
instalar-autofactory.bat       — primeira instalação
atualizar-autofactory.bat      — atualizar
iniciar.bat                    — usar dia a dia
LEIA-ME.txt                    — instruções pro liderado
```

**Como mandar:**
- Email com os 4 arquivos em anexo
- WhatsApp Documento
- Pasta compartilhada (Drive, OneDrive)
- Pendrive

**Tamanho total:** ~12 KB.

---

## Fluxo de uso pelo liderado

### Primeira vez (~5-10 min)

1. Liderado salva os 4 arquivos numa pasta tipo `C:\Users\NomeDele\Desktop\AutoFactory-Setup\`
2. Clica 2x em `instalar-autofactory.bat`
3. Janela preta abre, mostra progresso:
   - Verifica/instala Python (silencioso via winget)
   - Baixa zip do GitHub
   - Extrai
   - Cria venv, instala dependências
   - Instala Chromium do Playwright
   - Cria atalho na área de trabalho
4. Conclusão: aparece atalho "AutoFactory" na área de trabalho

### Dia a dia

1. Liderado clica no atalho **AutoFactory** (ou em `iniciar.bat`)
2. Servidor sobe em background
3. Navegador abre em `http://localhost:8000`
4. Liderado opera

**Atenção:** a janela preta do `iniciar.bat` precisa ficar aberta enquanto usa. Se fechar a janela, o sistema para.

### Atualizar

Quando você publica algo novo no GitHub:

1. Você avisa o liderado (WhatsApp, email)
2. Liderado fecha o AutoFactory (fecha a janela preta)
3. Clica 2x em `atualizar-autofactory.bat`
4. ~30 segundos → atualizado

**O que é preservado na atualização:**
- Credenciais GW pessoais (em `~\.automacao_factory\`)
- Histórico de operações
- Arquivos recentes (48h)
- Configuração de usuários

**O que é substituído:**
- Código do backend e frontend
- Dependências (se mudaram)

---

## Onde fica tudo no PC do liderado

```
C:\Users\<liderado>\AutoFactory\          ← pasta da aplicação
├── app\                                   ← código (substituído em atualizações)
│   ├── backend\
│   ├── frontend\
│   ├── .venv\                             ← Python virtual env
│   ├── requirements.txt
│   └── ...
├── instalar-autofactory.bat               ← (não usado depois da 1ª vez)
├── atualizar-autofactory.bat
├── iniciar.bat
└── LEIA-ME.txt

C:\Users\<liderado>\.automacao_factory\   ← DADOS DO USUÁRIO (preservados)
├── users.db                               ← cadastro local
├── operacoes_historico.json               ← histórico
└── arquivos_recentes\                     ← PDFs/ZIPs gerados (48h)

C:\Users\<liderado>\Desktop\
└── AutoFactory.lnk                        ← atalho criado pelo instalador
```

---

## Como você publica uma atualização

```bash
# No seu PC (admin), depois de testar localmente:
git add .
git commit -m "feat: ..."
git push origin main

# Avisa os liderados (WhatsApp/email):
# "Tem atualização nova! Fechem o AutoFactory e cliquem em 'atualizar-autofactory.bat'"
```

Não precisa criar release no GitHub — os scripts pegam direto o ZIP da branch `main`.

### Se quiser controlar versões

Crie um **GitHub Release** quando quiser marcar uma versão estável:
1. https://github.com/jonathasjga-cpu/automacao-factory/releases/new
2. Tag: `v1.0`, `v1.1`, etc.
3. Title + descrição do que mudou
4. "Publish release"

Depois, em vez do ZIP da branch, você pode fazer os scripts apontarem pro ZIP da release específica:
```
https://github.com/jonathasjga-cpu/automacao-factory/archive/refs/tags/v1.1.zip
```

Pra trocar isso, edite a linha `set ZIP_URL=...` em `instalar-autofactory.bat` e `atualizar-autofactory.bat`.

---

## Troubleshooting

### Liderado: "instalar.bat fecha sozinho rapidão"

Significa que deu erro silencioso. Faça o liderado abrir o **CMD manualmente** e rodar:
```
cd C:\caminho\onde\salvou\os\bat
instalar-autofactory.bat
```

Aí a janela fica aberta com a mensagem de erro.

### Liderado: "Python não instalou"

Algumas versões do Windows não têm `winget` ou bloquearam. Solução:
- Liderado baixa Python manualmente em https://www.python.org/downloads/
- Marca a opção **"Add Python to PATH"**
- Reinicia o PC
- Roda `instalar-autofactory.bat` de novo

### Liderado: "Porta 8000 em uso"

Outra instância do AutoFactory ainda está rodando. Força encerrar:
```cmd
taskkill /F /IM python.exe
```

Depois roda `iniciar.bat` de novo.

### Atualização: "robocopy retornou erro X"

Robocopy retorna códigos não-zero mesmo em sucesso (1=arquivos copiados, 2=arquivos extras detectados). Os scripts ignoram retorno do robocopy. Se realmente falhar, mensagem fica visível no log.

### Liderado: "Aparece tela preta do Cloudflare na FluxAsset"

Isso é normal. Liderado precisa **clicar no quadradinho do captcha** dentro de 3 minutos. A janela do Chrome com captcha aparece automaticamente quando rodar a operação FluxAsset.

---

## Limites conhecidos

1. **Apenas Windows 10/11**: scripts são `.bat` puros, não funcionam em Mac/Linux. Se algum liderado tem Mac, precisaria de versão `.sh` (não criada ainda).

2. **Liderado precisa de permissão de admin** na primeira instalação (pra Python instalar). Em PCs corporativos travados pode falhar — nesse caso, peça pro TI da empresa instalar Python uma vez.

3. **Sem auto-update**: liderado precisa rodar `atualizar.bat` manualmente. Se quiser auto-update na partida, dá pra adicionar em `iniciar.bat`.

4. **DB local por PC**: cada liderado tem seu próprio cadastro de usuário. Se um liderado trocar de PC, precisa cadastrar de novo. Não tem sincronização entre PCs.

5. **FluxAsset funciona local**: a partir desta instalação, FluxAsset funciona normalmente porque o Chrome local engana o Cloudflare. Quando rodar Railway, FluxAsset falha (limitação conhecida).
