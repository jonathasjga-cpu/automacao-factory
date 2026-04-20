# ⚡ AutoFactory

Automação de antecipação de faturas nas factories (Firma Capital, GC Recursos, FluxAsset).

---

## Como usar

### 1. Primeira vez (instalação)
- Tenha **Python 3.10+** instalado
- **Windows:** Clique duas vezes em `iniciar.bat`
- **Mac/Linux:** Execute `./iniciar.sh` no terminal

O script instala tudo automaticamente na primeira execução.

### 2. Configurar credenciais
Ao abrir, vá em **Configurações** e cadastre:
- GW Webtrans (usuário + senha)
- Firma Capital — Matriz
- Firma Capital — Filial SP
- GC Recursos *(quando disponível)*
- FluxAsset *(quando disponível)*

As senhas são salvas **criptografadas** no seu computador. Ninguém mais tem acesso.

### 3. Uso diário
1. Abra o sistema (`iniciar.bat` ou `iniciar.sh`)
2. Clique em **Carregar Faturas do GW** — o sistema baixa os dois relatórios automaticamente
3. Ajuste qual factory cada fatura vai (a sugestão é automática por filial)
4. Marque/desmarque as faturas que quiser incluir
5. Clique **Executar Selecionadas** — a automação roda em segundo plano
6. Quando concluída, confirme a finalização para encaminhar para operação

---

## Estrutura do projeto

```
automacao-factory/
├── backend/
│   ├── main.py                    ← Servidor API (FastAPI)
│   ├── config_manager.py          ← Gerenciamento de credenciais criptografadas
│   ├── requirements.txt           ← Dependências Python
│   └── services/
│       ├── excel_processor.py     ← Baixa e processa os Excels do GW
│       └── firma_automation.py    ← Automação da Firma Capital
├── frontend/
│   └── index.html                 ← Interface visual
├── iniciar.bat                    ← Inicialização Windows
└── iniciar.sh                     ← Inicialização Mac/Linux
```

---

## Fluxo técnico

```
GW Webtrans
  └─ Relatório "Automação Operações"   → Excel 1 (dados da fatura)
  └─ Relatório "Complemento Operações" → Excel 2 (chaves de acesso)
        ↓ cruzamento por Nº da Fatura
  └─ Lista unificada de faturas
        ↓ usuário revisa e seleciona
  Firma Capital (Matriz ou SP, conforme filial)
    └─ Login automático
    └─ Operação > Digitação > Novo
    └─ Para cada fatura:
        ├─ Se CNPJ não cadastrado: busca Receita Federal e cadastra
        └─ Preenche formulário completo e salva
    └─ Pausa → usuário confirma
    └─ Ações > Definir conta corrente (automático)
    └─ Encaminhar para operação / encerrar
```

---

## Campos mapeados GW → Firma Capital

| Campo Firma | Origem |
|---|---|
| CMC7/CPF/CNPJ | Excel 1 → Cliente CNPJ |
| Nome | Excel 1 → Cliente Razão Social |
| Vencimento | Excel 1 → Vencimento |
| Valor | Excel 1 → Total |
| Documento | Excel 1 → Número (nº fatura) |
| Núm.Nota | Excel 1 → Número (mesmo) |
| Dt.Emissão | Excel 1 → Emissão |
| Vlr.Nota | Excel 1 → Total (mesmo) |
| Chave | Excel 2 → Chave acesso CT-e (primeira) |

---

## Próximas factories a implementar
- [ ] GC Recursos (`services/gc_automation.py`)
- [ ] FluxAsset (`services/fluxasset_automation.py`)
