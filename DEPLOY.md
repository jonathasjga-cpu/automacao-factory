# Deploy no Railway

## 1. Subir pro GitHub

```bash
git add .
git commit -m "Auth + Dockerfile para deploy"
git push
```

## 2. Criar projeto no Railway

1. Acessa https://railway.app
2. **New Project** → **Deploy from GitHub repo** → escolhe esse repositório
3. Railway detecta o `Dockerfile` e começa o build

## 3. Adicionar Postgres

1. Dentro do projeto, clica **+ New** → **Database** → **Add PostgreSQL**
2. Railway cria e injeta a variável `DATABASE_URL` automaticamente (já tratada pelo código)

## 4. Variáveis de ambiente

No serviço do app, aba **Variables**, adicione:

| Nome | Valor | Obrigatório |
|---|---|---|
| `JWT_SECRET` | (gere uma string longa aleatória, ex: `openssl rand -hex 32`) | **SIM** — se não, a secret default do código é pública |
| `JWT_EXPIRE_HOURS` | `12` | Opcional (default 12h) |

> `DATABASE_URL` e `PORT` são injetados pelo Railway, não precisa setar.

## 5. Volume persistente (opcional mas recomendado)

O banco é Postgres, então os dados dos usuários já ficam seguros. Mas as credenciais das factories (GW, Firma, GC...) ainda são salvas em arquivo no `DATA_DIR`. Se quiser que essas credenciais sobrevivam a deploys:

1. No serviço, aba **Settings** → **Volumes** → **Add Volume**
2. Mount path: `/data`

## 6. Primeiro acesso

- URL do app: Railway mostra algo como `https://seu-app.up.railway.app`
- Login default: **`admin`** / **`admin123`**
- **Troque a senha imediatamente** em Usuários → Editar admin

## 7. Gerenciar usuários

Logado como admin, vai em **Sistema → Usuários**:
- Criar usuário comum (role = Usuário)
- Editar senha ou papel
- Desativar / excluir

---

## Estado conhecido

### ⚠️ Limitações após deploy

- **Download de Fatura PDF** não funciona em produção (usa popup do browser que só funciona localmente). Todo o resto das automações (operação Firma, FluxAsset, GC, geração do Excel, download de CT-es) roda em headless.
- **Pasta de destino local** só funciona rodando na máquina do usuário — em produção, os arquivos devem ser baixados pelo botão do front.

### Troque credenciais sensíveis

- `JWT_SECRET` — obrigatório em produção
- Senha do `admin` — troque no primeiro acesso
