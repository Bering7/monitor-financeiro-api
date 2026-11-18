import io
import os
import re
import uuid
import logging
import unicodedata
from typing import List, Optional, Any, Dict, Tuple

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client

# ==============================================================================
# 1. CONFIGURAÇÃO DE LOGGING & CARREGAMENTO DE VARIÁVEIS DE AMBIENTE
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("monitor_financeiro_api")

# Carrega variáveis do arquivo .env
load_dotenv()

# Tratamento para garantir compatibilidade caso haja variação no nome da variável de ambiente
SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("SUAPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_ANNON_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
)

# Inicializa o cliente do Supabase com service role para garantir permissões de gravação
supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        logger.info("Cliente Supabase inicializado com sucesso.")
    except Exception as e:
        logger.error(f"Falha ao conectar com o Supabase: {e}")
else:
    logger.warning(
        "Variáveis SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY não configuradas no .env. "
        "A persistência no banco ficará desabilitada até a configuração."
    )

# ==============================================================================
# 2. INICIALIZAÇÃO DO FASTAPI & CONFIGURAÇÃO DE CORS
# ==============================================================================
app = FastAPI(
    title="Monitor Financeiro API",
    description="Microsserviço de processamento de extratos bancários com auto-categorização inteligente e integração com Supabase.",
    version="1.0.0"
)

# Configuração de CORS aberta para integração com frontends (ex: Vercel)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# 3. MODELOS PYDANTIC (SCHEMAS)
# ==============================================================================
class TransacaoItem(BaseModel):
    id_temp: Optional[str] = Field(None, description="Identificador temporário gerado para controle na prévia")
    user_id: Optional[str] = Field(None, description="ID do usuário proprietário da transação")
    data: str = Field(..., description="Data da transação (formato YYYY-MM-DD)")
    descricao: str = Field(..., description="Descrição original tratada da movimentação")
    valor: float = Field(..., description="Valor numérico (positivo para receitas, negativo para despesas na prévia)")
    tipo: str = Field(..., description="'Receita' ou 'Despesa'")
    categoria: str = Field(..., description="Categoria atribuída automaticamente ou revisada")

class ResumoExtrato(BaseModel):
    total_registros: int
    total_receitas: float
    total_despesas: float
    saldo_periodo: float

class ProcessarExtratoResponse(BaseModel):
    sucesso: bool
    mensagem: str
    resumo: ResumoExtrato
    transacoes: List[TransacaoItem]

class SalvarLoteRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="User ID padrão para os itens sem ID específico")
    transacoes: List[TransacaoItem] = Field(..., description="Lista de transações confirmadas para persistência")

# ==============================================================================
# 4. REGRAS DO MOTOR DE AUTO-CATEGORIZAÇÃO POR PALAVRAS-CHAVE
# ==============================================================================
# Categorias de Despesas por palavras-chave solicitadas e comuns
REGRAS_CATEGORIAS = [
    (
        "Transporte",
        [
            "UBER", "99", "POSTO", "GASOLINA", "COMBUSTIVEL", "ESTACIONAMENTO",
            "PEDAGIO", "AUTO POSTO", "SEM PARAR", "VELOE", "CONECTCAR", "CARREFOUR POSTO"
        ]
    ),
    (
        "Alimentação",
        [
            "IFOOD", "SUPERMERCADO", "PADARIA", "MERCADO", "RESTAURANTE",
            "BAR", "LANCHONETE", "PIZZARIA", "BURGER", "DELIVERY", "CAFETERIA",
            "ASSAI", "ATACADAO", "CARREFOUR", "PAO DE ACUCAR", "EXTRA", "AÇOUGUE", "ACOUGUE"
        ]
    ),
    (
        "Lazer / Assinaturas",
        [
            "NETFLIX", "SPOTIFY", "AMAZON", "PRIME", "DISNEY", "HBO", "MAX",
            "CINEMA", "STEAM", "PLAYSTATION", "XBOX", "YOUTUBE", "APPLE.COM/BILL", "DEEZER"
        ]
    ),
    (
        "Saúde / Farmácia",
        [
            "FARMACIA", "DROGARIA", "DROGASIL", "RAIA", "CONSULTA", "MEDICO",
            "HOSPITAL", "LABORATORIO", "EXAME", "DENTISTA", "CLINICA"
        ]
    ),
    (
        "Serviços / Moradia",
        [
            "LUZ", "ENERGIA", "AGUA", "CONDOMINIO", "ALUGUEL", "INTERNET",
            "CLARO", "VIVO", "TIM", "ENEL", "SABESP", "GAS", "CPFL", "LIGHT"
        ]
    )
]

# Palavras-chave que identificam inequivocamente ENTRADAS / RECEITAS de caixa
# (Garantem que valores positivos como Pix recebidos, estornos e resgates de CDB sejam contabilizados)
PALAVRAS_ENTRADA_GARANTIDA = [
    "ESTORNO", "RESGATE", "PIX RECEBIDO", "PIX REC", "TED RECEBIDA", "TED REC",
    "DOC RECEBIDA", "DOC REC", "TRANSFERENCIA RECEBIDA", "TRANSF RECEBIDA",
    "DEPOSITO RECEBIDO", "DEP RECEBIDO", "SALARIO", "REMUNERACAO", "PRO-LABORE",
    "RENDIMENTO", "REEMBOLSO", "DIVIDENDOS", "PROVENTOS", "CDB LIQUIDEZ",
    "APLICACAO RESGATE", "RESGATE INVEST", "CREDITO DE", "RECEBIMENTO"
]

def auto_categorizar_transacao(descricao: str, valor: float) -> Tuple[str, str, float]:
    """
    Classifica a transação em 'Receita' ou 'Despesa', aplica o motor de palavras-chave
    e assegura que estornos, resgates de investimentos e recebimentos sejam tratados como
    entradas de caixa positivas (Receita).
    Retorna uma tupla (tipo, categoria, valor_ajustado).
    """
    desc_limpa = (descricao or "").upper().strip()
    
    # 1. Verifica se a transação é garantidamente uma ENTRADA (Receita / Inflow)
    is_entrada_garantida = any(termo in desc_limpa for termo in PALAVRAS_ENTRADA_GARANTIDA)
    
    if is_entrada_garantida:
        tipo = "Receita"
        valor_ajustado = abs(valor)  # Força sinal positivo para entradas de caixa
        
        if "ESTORNO" in desc_limpa:
            # Identifica se é estorno de uma categoria conhecida
            for cat, keywords in REGRAS_CATEGORIAS:
                if any(kw in desc_limpa for kw in keywords):
                    return tipo, f"Estorno ({cat})", valor_ajustado
            return tipo, "Estorno / Reembolso", valor_ajustado
        elif "RESGATE" in desc_limpa or "CDB" in desc_limpa:
            return tipo, "Resgate / Investimento", valor_ajustado
        elif any(termo in desc_limpa for termo in ["SALARIO", "REMUNERACAO", "PRO-LABORE"]):
            return tipo, "Salário / Renda", valor_ajustado
        elif any(termo in desc_limpa for termo in ["RENDIMENTO", "DIVIDENDOS", "PROVENTOS"]):
            return tipo, "Rendimentos / Investimentos", valor_ajustado
        elif "REEMBOLSO" in desc_limpa:
            return tipo, "Reembolso", valor_ajustado
        else:
            return tipo, "Receitas / Transferências", valor_ajustado

    # 2. Se o valor for positivo (>= 0), é uma Receita
    if valor >= 0:
        tipo = "Receita"
        valor_ajustado = valor
        for cat, keywords in REGRAS_CATEGORIAS:
            if any(kw in desc_limpa for kw in keywords):
                return tipo, f"Receita ({cat})", valor_ajustado
        return tipo, "Receita", valor_ajustado

    # 3. Categorização de Despesas (valor < 0)
    tipo = "Despesa"
    valor_ajustado = valor  # Mantém negativo para visualização na prévia

    for cat, keywords in REGRAS_CATEGORIAS:
        if any(kw in desc_limpa for kw in keywords):
            return tipo, cat, valor_ajustado

    # Categoria padrão de fallback
    return tipo, "Outros / Despesa Variável", valor_ajustado

# ==============================================================================
# 5. FUNÇÕES AUXILIARES DE PROCESSAMENTO DE DADOS (PANDAS & LIMPEZA)
# ==============================================================================
def limpar_valor_monetario(val: Any) -> float:
    """
    Converte com segurança valores monetários (formatos brasileiros ou internacionais)
    para float de precisão decimal.
    Exemplos:
      - '1.250,50'   -> 1250.50
      - '-1.250,50'  -> -1250.50
      - 'R$ 1.250,50'-> 1250.50
      - '+2.600,00'  -> 2600.00
      - '2.600,00'   -> 2600.00
      - '2.600'      -> 2600.00
      - '1.250,50 D' -> -1250.50
      - '1.250,50 C' -> 1250.50
      - '(150,00)'   -> -150.00
      - '1250.50'    -> 1250.50
    """
    if pd.isna(val) or val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    if not s:
        return 0.0

    s_upper = s.upper()
    is_negative = False
    
    # Detecção de saídas/débitos/valores negativos
    if (
        s.startswith("-")
        or "(" in s
        or s_upper.endswith("D")
        or "DEBITO" in s_upper
        or "DÉBITO" in s_upper
    ):
        is_negative = True

    # Remove qualquer caracter exceto dígitos, vírgula, ponto e sinal negativo
    cleaned = re.sub(r"[^\d,\.-]", "", s)
    if not cleaned:
        return 0.0

    # Conversão de separador decimal e milhar
    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # Padrão brasileiro: 1.250,50 -> 1250.50
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Padrão internacional: 1,250.50 -> 1250.50
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Apenas vírgula como decimal: 150,50 -> 150.50
        cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        # Se contiver apenas ponto, verifica se é milhar sem centavos (ex: 2.600)
        parts = cleaned.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and not ("," in s):
            cleaned = parts[0] + parts[1]

    try:
        resultado = float(cleaned)
        if is_negative and resultado > 0:
            resultado = -resultado
        return round(resultado, 2)
    except (ValueError, TypeError):
        return 0.0

def converter_data_iso(val: Any) -> str:
    """
    Converte datas de extratos bancários brasileiros para o formato padrão ISO 8601 (YYYY-MM-DD).
    """
    if pd.isna(val) or val is None:
        return ""
    
    s = str(val).strip()
    if not s:
        return ""

    try:
        # dayfirst=True para interpretar corretamente o formato DD/MM/AAAA
        dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.notna(dt):
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return s

def ler_arquivo_csv(conteudo_bytes: bytes) -> pd.DataFrame:
    """
    Lê o conteúdo do arquivo CSV tratando codificações comuns (utf-8-sig, utf-8, latin1, cp1252)
    e identificando com precisão cirúrgica a linha do cabeçalho da tabela (ignorando metadados de bancos).
    Força e prioriza o delimitador ';' utilizado nos extratos de bancos brasileiros como o Banco Inter.
    """
    encodings = ["utf-8-sig", "utf-8", "latin1", "cp1252", "iso-8859-1"]
    texto_decodificado = None

    for enc in encodings:
        try:
            texto_decodificado = conteudo_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue

    if texto_decodificado is None:
        texto_decodificado = conteudo_bytes.decode("latin1", errors="replace")

    linhas = texto_decodificado.strip().splitlines()
    if not linhas:
        raise ValueError("O arquivo CSV enviado está vazio.")

    # Termos característicos das colunas de tabelas de extrato bancário
    termos_coluna = [
        "data", "dt", "historico", "descricao", "valor", "saldo",
        "lancamento", "debito", "credito", "documento", "movimento"
    ]

    melhor_indice_cabecalho = 0
    melhor_pontuacao = 0
    delimitador_escolhido = ";"

    # Varre as primeiras 35 linhas buscando a linha que mais contém termos típicos de cabeçalho
    for idx, linha in enumerate(linhas[:35]):
        linha_limpa = linha.strip()
        if not linha_limpa:
            continue

        for delim in [";", ",", "\t"]:
            if delim not in linha_limpa:
                continue

            tokens = [t.strip() for t in linha_limpa.split(delim) if t.strip()]
            if len(tokens) < 2:
                continue

            pontos = 0
            for t in tokens:
                t_norm = unicodedata.normalize('NFKD', t).encode('ASCII', 'ignore').decode('utf-8').lower()
                t_norm = re.sub(r'[^a-z0-9]', '', t_norm)
                if any(termo in t_norm for termo in termos_coluna):
                    pontos += 1

            if pontos > melhor_pontuacao:
                melhor_pontuacao = pontos
                melhor_indice_cabecalho = idx
                delimitador_escolhido = delim

    logger.info(
        f"Cabeçalho identificado na linha {melhor_indice_cabecalho} "
        f"(pontuação={melhor_pontuacao}, delimitador='{delimitador_escolhido}'): "
        f"'{linhas[melhor_indice_cabecalho]}'"
    )

    texto_para_processar = "\n".join(linhas[melhor_indice_cabecalho:])

    # Tenta ler forçando o delimitador identificado (prioridade absoluta para ';')
    df_resultado = None
    tentativas_delim = [delimitador_escolhido]
    if ";" not in tentativas_delim:
        tentativas_delim.append(";")
    tentativas_delim.extend([",", "\t", None])

    for delim in tentativas_delim:
        try:
            df = pd.read_csv(
                io.StringIO(texto_para_processar),
                sep=delim,
                engine="python" if delim is None else "c",
                dtype=str,
                on_bad_lines="skip"
            )
            # Remove eventuais colunas totalmente vazias geradas por delimitador no fim da linha
            df = df.dropna(how="all", axis=1)
            if len(df.columns) >= 2 and len(df) > 0:
                df_resultado = df
                break
        except Exception as e:
            logger.debug(f"Falha na tentativa com delimitador '{delim}': {e}")
            continue

    if df_resultado is None:
        df_resultado = pd.read_csv(
            io.StringIO(texto_para_processar),
            sep=None,
            engine="python",
            dtype=str
        )
        df_resultado = df_resultado.dropna(how="all", axis=1)

    return df_resultado

def mapear_colunas_extrato(df: pd.DataFrame) -> Tuple[str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Mapeia automaticamente colunas de extratos bancários brasileiros:
    Data, Descrição, Valor, Débito, Crédito, Histórico e Saldo.
    Garante que a coluna de Saldo NUNCA seja lida como coluna de Valor.
    """
    col_data = None
    col_descricao = None
    col_valor = None
    col_debito = None
    col_credito = None
    col_historico = None
    col_saldo = None

    def normalizar(col_nome: str) -> str:
        s = unicodedata.normalize('NFKD', str(col_nome)).encode('ASCII', 'ignore').decode('utf-8')
        return re.sub(r"[^a-zA-Z0-9]", "", s).lower()

    colunas_map = {col: normalizar(col) for col in df.columns}

    # 1. Identificar Coluna de Saldo (para ISOLAR e NUNCA ler como valor de movimentação)
    for col, norm in colunas_map.items():
        if "saldo" in norm or "balance" in norm:
            col_saldo = col
            break

    # 2. Identificar Coluna de Data
    for col, norm in colunas_map.items():
        if norm in ["data", "date", "dt", "datalancamento", "datamovimento", "dtmovimento", "dtlancamento"]:
            col_data = col
            break
    if not col_data:
        for col, norm in colunas_map.items():
            if ("data" in norm or "date" in norm or norm.startswith("dt")) and col != col_saldo:
                col_data = col
                break

    # 3. Identificar Colunas separadas de Débito e Crédito
    for col, norm in colunas_map.items():
        if col in [col_data, col_saldo]:
            continue
        if "debito" in norm or norm in ["saida", "saidas"]:
            col_debito = col
        elif "credito" in norm or norm in ["entrada", "entradas"]:
            col_credito = col

    # 4. Identificar Coluna de Valor Único (NUNCA pode ser col_saldo)
    # Prioridade 1: Exatamente 'valor', 'valorr$', 'valortransacao'
    for col, norm in colunas_map.items():
        if col in [col_data, col_saldo] or "saldo" in norm or "balance" in norm:
            continue
        if norm in ["valor", "valorr", "valorrs", "value", "amount", "quantia", "valortransacao", "valormovimento", "valorlancamento"]:
            col_valor = col
            break

    # Prioridade 2: Contém 'valor', 'amount', 'quantia'
    if not col_valor:
        for col, norm in colunas_map.items():
            if col in [col_data, col_saldo] or "saldo" in norm or "balance" in norm:
                continue
            if ("valor" in norm or "amount" in norm or "quantia" in norm):
                col_valor = col
                break

    # 5. Identificar Coluna de Histórico e Coluna de Descrição
    for col, norm in colunas_map.items():
        if col in [col_data, col_valor, col_saldo, col_debito, col_credito]:
            continue
        if "historico" in norm or norm.startswith("hist"):
            col_historico = col
        elif any(termo in norm for termo in ["desc", "titulo", "detalhe", "memo", "estab", "transacao"]):
            col_descricao = col

    # Preenchimento de colunas restantes se necessário
    sobras = [
        c for c in df.columns
        if c not in [col_data, col_valor, col_saldo, col_debito, col_credito, col_historico, col_descricao]
    ]

    if not col_descricao and not col_historico:
        if sobras:
            col_descricao = sobras[0]
    elif not col_descricao and col_historico and sobras:
        col_descricao = sobras[0]
    elif not col_historico and col_descricao and sobras:
        col_historico = sobras[0]

    # Validação mínima obrigatória de col_data
    if not col_data:
        candidatos = [c for c in df.columns if c != col_saldo]
        col_data = candidatos[0] if candidatos else df.columns[0]

    # Se col_valor não foi achado e não há debito/credito, busca coluna numérica excluindo saldo
    if not col_valor and not (col_debito and col_credito):
        candidatos = [
            c for c in df.columns
            if c not in [col_data, col_saldo, col_descricao, col_historico] and "saldo" not in colunas_map.get(c, "")
        ]
        if candidatos:
            col_valor = candidatos[-1]
        else:
            if df.columns[-1] != col_saldo and "saldo" not in colunas_map.get(df.columns[-1], ""):
                col_valor = df.columns[-1]

    return col_data, col_descricao, col_valor, col_debito, col_credito, col_historico, col_saldo

# ==============================================================================
# 6. ROTAS DA API
# ==============================================================================

@app.get("/", tags=["Health"])
async def root():
    """Rota raiz para verificação rápida de status e conexão com Supabase."""
    return {
        "status": "online",
        "servico": "Monitor Financeiro API",
        "versao": "1.0.0",
        "supabase_conectado": supabase is not None
    }

@app.get("/health", tags=["Health"])
async def health_check():
    """Endpoint de health check para monitoramento de infraestrutura."""
    return {"status": "ok"}

@app.post(
    "/processar-extrato",
    response_model=ProcessarExtratoResponse,
    status_code=status.HTTP_200_OK,
    tags=["Extrato"]
)
async def processar_extrato(
    file: UploadFile = File(..., description="Arquivo CSV do extrato bancário"),
    user_id: Optional[str] = Form(None, description="ID do usuário proprietário do extrato (opcional via Form)"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id", description="ID do usuário via Header (alternativo)")
):
    """
    Recebe um extrato bancário em CSV, lê com Pandas pulando metadados de bancos (ex: Banco Inter),
    mapeia automaticamente colunas de data/histórico/descrição/valor, aplica auto-categorização inteligente
    assegurando a contabilização de entradas, estornos e resgates, e devolve a prévia estruturada.
    """
    # 1. Validação da extensão do arquivo
    nome_arquivo = file.filename or ""
    if not nome_arquivo.lower().endswith((".csv", ".txt")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de arquivo inválido. Por favor, envie um arquivo .csv ou .txt."
        )

    resolved_user_id = user_id or x_user_id

    try:
        # Lê os bytes do arquivo enviado
        conteudo_bytes = await file.read()
        if not conteudo_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O arquivo enviado está vazio."
            )

        # 2. Leitura com Pandas tratando delimitadores e ignorando metadados
        df = ler_arquivo_csv(conteudo_bytes)
        if df.empty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Não foi possível identificar linhas de transação válidas no arquivo CSV."
            )

        # 3. Mapeamento automático de colunas isolando col_saldo de col_valor
        col_data, col_desc, col_valor, col_deb, col_cred, col_hist, col_saldo = mapear_colunas_extrato(df)
        logger.info(
            f"Colunas mapeadas: Data='{col_data}', Descrição='{col_desc}', Histórico='{col_hist}', "
            f"Valor='{col_valor}', Saldo='{col_saldo}' (ignorado no cálculo), Débito='{col_deb}', Crédito='{col_cred}'"
        )

        transacoes: List[TransacaoItem] = []
        soma_receitas = 0.0
        soma_despesas = 0.0

        # 4. Processamento linha a linha das transações
        for _, row in df.iterrows():
            raw_data = row.get(col_data)
            raw_desc = row.get(col_desc) if col_desc and col_desc in row else None
            raw_hist = row.get(col_hist) if col_hist and col_hist in row else None

            # Ignora linhas totalmente nulas
            if pd.isna(raw_data) and pd.isna(raw_desc) and pd.isna(raw_hist):
                continue

            data_iso = converter_data_iso(raw_data)

            # Monta descrição unificada (Histórico + Descrição)
            partes_desc = []
            if raw_hist and pd.notna(raw_hist):
                h_str = str(raw_hist).strip()
                if h_str and h_str.lower() != "nan":
                    partes_desc.append(h_str)

            if raw_desc and pd.notna(raw_desc):
                d_str = str(raw_desc).strip()
                if d_str and d_str.lower() != "nan":
                    if not partes_desc or d_str.upper() != partes_desc[0].upper():
                        partes_desc.append(d_str)

            descricao = " - ".join(partes_desc) if partes_desc else ""

            # Descarta linhas sem informações mínimas
            if not data_iso and not descricao:
                continue

            # Cálculo do valor numérico (NUNCA usa a coluna de saldo)
            if col_valor and col_valor in row:
                valor_float = limpar_valor_monetario(row[col_valor])
            elif col_deb and col_cred and (col_deb in row or col_cred in row):
                debito = abs(limpar_valor_monetario(row.get(col_deb)))
                credito = abs(limpar_valor_monetario(row.get(col_cred)))
                valor_float = round(credito - debito, 2)
            else:
                valor_float = 0.0

            # Desconsidera linhas de valor zerado e sem descrição
            if valor_float == 0.0 and not descricao:
                continue

            # 5. Aplicação do Motor de Categorização com garantia de entradas para Estornos e Resgates
            tipo, categoria, valor_ajustado = auto_categorizar_transacao(descricao, valor_float)

            # Acumula totais do resumo (Entradas/Receitas vs Despesas)
            if tipo == "Receita" or valor_ajustado >= 0:
                soma_receitas += abs(valor_ajustado)
            else:
                soma_despesas += abs(valor_ajustado)

            item = TransacaoItem(
                id_temp=str(uuid.uuid4()),
                user_id=resolved_user_id,
                data=data_iso,
                descricao=descricao,
                valor=valor_ajustado,
                tipo=tipo,
                categoria=categoria
            )
            transacoes.append(item)

        total_processados = len(transacoes)
        if total_processados == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nenhuma transação financeira válida pôde ser extraída do arquivo enviado."
            )

        resumo = ResumoExtrato(
            total_registros=total_processados,
            total_receitas=round(soma_receitas, 2),
            total_despesas=round(soma_despesas, 2),
            saldo_periodo=round(soma_receitas - soma_despesas, 2)
        )

        return ProcessarExtratoResponse(
            sucesso=True,
            mensagem=f"Extrato processado com sucesso! {total_processados} transações identificadas.",
            resumo=resumo,
            transacoes=transacoes
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro inesperado ao processar extrato CSV.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno ao processar o arquivo: {str(e)}"
        )

@app.post(
    "/salvar-lote",
    status_code=status.HTTP_201_CREATED,
    tags=["Transações"]
)
async def salvar_lote(payload: SalvarLoteRequest):
    """
    Persiste em lote na tabela 'transacoes' do Supabase as transações revisadas e aprovadas pelo usuário.
    Normaliza os tipos e valores para garantir compatibilidade com as restrições do banco.
    """
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A integração com o Supabase não está configurada no servidor. Verifique as credenciais no .env."
        )

    if not payload.transacoes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A lista de transações para salvar está vazia."
        )

    try:
        registros_para_inserir = []
        for item in payload.transacoes:
            user_id_final = item.user_id or payload.user_id
            if not user_id_final:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="user_id é obrigatório para persistir as transações no Supabase."
                )

            # Normalização de tipo para conformidade com a constraint do Supabase:
            # ('receita', 'despesa-variavel', 'despesa-fixa')
            tipo_normalizado = item.tipo.lower().strip()
            if "receita" in tipo_normalizado:
                tipo_banco = "receita"
            elif "fixa" in tipo_normalizado:
                tipo_banco = "despesa-fixa"
            else:
                tipo_banco = "despesa-variavel"

            # No banco as despesas e receitas são mantidas com valor absoluto positivo,
            # diferenciadas pela coluna 'tipo'
            valor_positivo = abs(float(item.valor))

            registro = {
                "user_id": user_id_final,
                "data": item.data,
                "descricao": item.descricao,
                "valor": valor_positivo,
                "tipo": tipo_banco,
                "categoria": item.categoria,
                "futuro": False,
                "sem_data": False,
                "valor_fixo": True if tipo_banco == "despesa-fixa" else False
            }
            registros_para_inserir.append(registro)

        # Inserção em lote no Supabase
        resultado = supabase.table("transacoes").insert(registros_para_inserir).execute()

        logger.info(f"{len(registros_para_inserir)} transações inseridas com sucesso no Supabase.")

        return {
            "sucesso": True,
            "mensagem": f"{len(registros_para_inserir)} transações gravadas com sucesso no Supabase.",
            "total_salvo": len(registros_para_inserir),
            "dados": resultado.data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro ao salvar lote de transações no Supabase.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao persistir dados no Supabase: {str(e)}"
        )
