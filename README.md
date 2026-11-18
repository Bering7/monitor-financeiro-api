# 💰 Monitor Financeiro API

Microsserviço backend em **Python** desenvolvido com **FastAPI** e **Pandas**, responsável pela ingestão, processamento resiliente de extratos bancários em formato CSV, auto-categorização inteligente de despesas/receitas por palavras-chave e persistência segura no banco de dados **Supabase**.

---

## 📌 Visão Geral

A **Monitor Financeiro API** foi projetada para resolver a dor de importar manualmente transações financeiras de diferentes bancos brasileiros (Nubank, Itaú, Bradesco, Banco do Brasil, Inter, Caixa, Santander, etc.). 

### Principais Funcionalidades:
- **Parser Resiliente de CSVs**: Trata automaticamente múltiplos encodings (`utf-8`, `utf-8-sig`, `latin1`/`iso-8859-1`, `cp1252`) e delimitadores (`;`, `,`, `\t`).
- **Limpeza de Metadados Bancários**: Ignora linhas iniciais de cabeçalhos de bancos com informações de agência/conta antes do cabeçalho da tabela.
- **Normalização de Moeda Brasileira**: Converte formatos como `R$ -1.250,50`, `1.250,50 D`, `(150,00)` e valores com sinal para números decimais padrão (`float`).
- **Motor de Auto-Categorização**: Mapeia descrições em caixa alta para categorias semânticas (Transporte, Alimentação, Lazer/Assinaturas, etc.) e determina se é `Receita` ou `Despesa`.
- **Prévia para Revisão**: Retorna os dados tratados com resumo consolidado antes da confirmação de gravação definitiva.
- **Persistência em Lote no Supabase**: Endpoint dedicado para salvar em massa os registros validados na tabela `transacoes`.
- **CORS Liberado**: Permite integração com frontends modernos hospedados na Vercel ou outras plataformas.

---

## 🛠️ Tecnologias Utilizadas

- **[Python 3.10+](https://www.python.org/)**: Linguagem base do microsserviço.
- **[FastAPI](https://fastapi.tiangolo.com/)**: Framework web assíncrono de alto desempenho para criação de APIs.
- **[Pandas](https://pandas.pydata.org/)**: Biblioteca para leitura, parsing, manipulação e limpeza vetorial de dados tabulares.
- **[Supabase Python Client](https://supabase.com/docs/reference/python/introduction)**: Cliente oficial para integração e persistência de dados no Postgres/Supabase.
- **[Uvicorn](https://www.uvicorn.org/)**: Servidor ASGI rápido para execução da aplicação.
- **[Pydantic v2](https://docs.pydantic.dev/)**: Validação estrita de tipos e serialização de dados.
- **[python-dotenv](https://github.com/theskumar/python-dotenv)**: Gerenciamento seguro de variáveis de ambiente.
- **[python-multipart](https://github.com/Kludex/python-multipart)**: Suporte para upload de arquivos multipart/form-data.

---

## ⚙️ Pré-requisitos

Antes de iniciar, certifique-se de ter instalado em sua máquina:
- **Python** (versão 3.10 ou superior)
- **Git**

---

## 🚀 Instalação e Configuração

### 1. Clonar o Repositório
```bash
git clone https://github.com/seu-usuario/monitor-financeiro-api.git
cd monitor-financeiro-api
```

### 2. Criar e Ativar o Ambiente Virtual (`.venv`)

**No Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

*(Se encontrar restrição de execução de scripts no PowerShell, execute antes: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`)*

**No Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Instalar as Dependências
Com o ambiente virtual ativado, instale os pacotes listados em `requirements.txt`:
```bash
pip install -r requirements.txt
```

---

## 🔐 Configuração do Ambiente (`.env`)

Crie um arquivo `.env` na raiz do projeto (ou copie o modelo `.env.example`):

```bash
cp .env.example .env
```

Preencha com as credenciais do seu projeto no **Supabase**:

```env
# URL base do seu projeto Supabase
SUPABASE_URL=https://seu-projeto.supabase.co

# Chave anônima / pública (opcional para o backend)
SUPABASE_ANON_KEY=sua-chave-anon-aqui

# Chave de serviço (Service Role) - obrigatória para operações com permissão de escrita
SUPABASE_SERVICE_ROLE_KEY=sua-chave-service-role-aqui
```

> **Atenção:** Nunca compartilhe ou faça commit do arquivo `.env` contendo a `SUPABASE_SERVICE_ROLE_KEY` em repositórios públicos. O arquivo `.env` já está devidamente listado no `.gitignore`.

---

## 🖥️ Como Executar o Servidor

Com as dependências instaladas e o `.env` configurado, inicie o servidor localmente:

```bash
uvicorn main:app --reload --port 8000
```

O servidor iniciará em `http://localhost:8000`.

### Documentação Interativa da API
Acesse no seu navegador:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 📡 Endpoints da API

### 1. Verificação de Saúde / Raiz

#### `GET /`
Retorna o status geral do microsserviço e se o cliente do Supabase está conectado.

**Exemplo de Resposta:**
```json
{
  "status": "online",
  "servico": "Monitor Financeiro API",
  "versao": "1.0.0",
  "supabase_conectado": true
}
```

#### `GET /health`
Endpoint leve para monitoramento de disponibilidade e health checks de hospedagem (Render, Railway, Fly.io, etc.).

---

### 2. Processamento de Extrato Bancário

#### `POST /processar-extrato`
Recebe o extrato bancário em formato CSV via `multipart/form-data`, processa os registros, auto-categoriza e retorna a prévia formatada.

- **Content-Type:** `multipart/form-data`
- **Parâmetros:**
  - `file` *(obrigatório)*: Arquivo CSV ou TXT do extrato bancário.
  - `user_id` *(opcional via Form)*: UUID do usuário autenticado.
  - `X-User-Id` *(opcional via Header)*: UUID do usuário alternativo via cabeçalho HTTP.

**Exemplo de Resposta (200 OK):**
```json
{
  "sucesso": true,
  "mensagem": "Extrato processado com sucesso! 3 transações identificadas.",
  "resumo": {
    "total_registros": 3,
    "total_receitas": 3500.0,
    "total_despesas": 74.4,
    "saldo_periodo": 3425.6
  },
  "transacoes": [
    {
      "id_temp": "e30e71ab-1fcf-49b8-b11c-d784a98404a3",
      "user_id": "eab35ae5-b718-4e33-9738-aee6afd78d9a",
      "data": "2026-02-10",
      "descricao": "UBER *TRIP",
      "valor": -24.50,
      "tipo": "Despesa",
      "categoria": "Transporte"
    },
    {
      "id_temp": "4c94e022-d7ae-4f91-8848-0ca9de1015f6",
      "user_id": "eab35ae5-b718-4e33-9738-aee6afd78d9a",
      "data": "2026-02-11",
      "descricao": "IFOOD *RESTAURANTE",
      "valor": -49.90,
      "tipo": "Despesa",
      "categoria": "Alimentação"
    },
    {
      "id_temp": "9b12d5e1-0cf2-4ab1-8e5e-ef17c24483a2",
      "user_id": "eab35ae5-b718-4e33-9738-aee6afd78d9a",
      "data": "2026-02-12",
      "descricao": "SALARIO EMPRESA XYZ",
      "valor": 3500.00,
      "tipo": "Receita",
      "categoria": "Receitas / Renda"
    }
  ]
}
```

---

### 3. Gravação em Lote no Supabase

#### `POST /salvar-lote`
Recebe a lista de transações revisadas e confirmadas pelo usuário no frontend e efetua a inserção em lote na tabela `transacoes` do Supabase.

- **Content-Type:** `application/json`
- **Body:**
```json
{
  "user_id": "eab35ae5-b718-4e33-9738-aee6afd78d9a",
  "transacoes": [
    {
      "data": "2026-02-10",
      "descricao": "UBER *TRIP",
      "valor": -24.50,
      "tipo": "Despesa",
      "categoria": "Transporte"
    },
    {
      "data": "2026-02-11",
      "descricao": "IFOOD *RESTAURANTE",
      "valor": -49.90,
      "tipo": "Despesa",
      "categoria": "Alimentação"
    }
  ]
}
```

**Exemplo de Resposta (201 Created):**
```json
{
  "sucesso": true,
  "mensagem": "2 transações gravadas com sucesso no Supabase.",
  "total_salvo": 2,
  "dados": [
    {
      "id": "7ac19d2b-58d7-4659-86ec-3507cfb7a5a8",
      "user_id": "eab35ae5-b718-4e33-9738-aee6afd78d9a",
      "data": "2026-02-10",
      "descricao": "UBER *TRIP",
      "valor": 24.50,
      "tipo": "despesa-variavel",
      "categoria": "Transporte"
    }
  ]
}
```

---

## 🏷️ Regras de Auto-Categorização

O motor de classificação analisa a descrição (em caixa alta) e o sinal numérico:

| Categoria | Palavras-Chave de Exemplo |
| :--- | :--- |
| **Transporte** | `UBER`, `99`, `POSTO`, `GASOLINA`, `COMBUSTIVEL`, `PEDAGIO`, `AUTO POSTO`, `SEM PARAR` |
| **Alimentação** | `IFOOD`, `SUPERMERCADO`, `PADARIA`, `MERCADO`, `RESTAURANTE`, `BAR`, `BURGER`, `PIZZARIA` |
| **Lazer / Assinaturas** | `NETFLIX`, `SPOTIFY`, `AMAZON`, `PRIME`, `DISNEY`, `HBO`, `CINEMA`, `STEAM`, `YOUTUBE` |
| **Saúde / Farmácia** | `FARMACIA`, `DROGARIA`, `DROGASIL`, `RAIA`, `CONSULTA`, `MEDICO`, `HOSPITAL`, `EXAME` |
| **Serviços / Moradia** | `LUZ`, `ENERGIA`, `AGUA`, `CONDOMINIO`, `ALUGUEL`, `INTERNET`, `CLARO`, `VIVO`, `TIM`, `SABESP` |
| **Receitas / Renda** | `SALARIO`, `PRO-LABORE`, `RENDIMENTO`, `PIX RECEBIDO`, `TED RECEBIDA`, `DIVIDENDOS` |
| **Outros / Despesa Variável** | Fallback para despesas que não casarem com as regras acima |

---

## 📁 Estrutura de Pastas

```text
monitor-financeiro-api/
├── .env                  # Variáveis de ambiente (ignorado no Git)
├── .env.example          # Exemplo de configuração de variáveis
├── .gitignore            # Arquivos e diretórios ignorados pelo Git
├── main.py               # Aplicação FastAPI, rotas e regras de negócio
├── Procfile              # Comando de inicialização para deploy (PaaS / Docker)
├── requirements.txt      # Dependências do projeto
└── README.md             # Documentação do projeto
```

---

## 📄 Licença

Este projeto é desenvolvido para fins de controle e monitoramento financeiro pessoal.
