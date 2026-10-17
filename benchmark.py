"""
Benchmark PostgreSQL (JSONB) vs MongoDB: roda a Seção 7 (Escalabilidade) do notebook
`notebooks/analises.ipynb`, sem a Seção 6 (consultas individuais) e sem Jupyter.

As funções de consulta/escrita são as mesmas do notebook. Os resultados vão para JSON.

Como as medições são feitas:
  - Leituras (C1, C4, C5, C6, C7): antes de CADA repetição (fora da cronometragem) os containers são
    parados, o cache de páginas do sistema operacional é descartado (precisa de sudo) e os containers
    sobem de novo; só então a consulta é cronometrada. Não há execução de aquecimento.
  - Escritas (C2, C3, C8) e o experimento de lote: o mesmo procedimento das leituras (cache limpo antes de
    cada repetição, sem aquecimento), restaurando o estado original antes de limpar o cache. Assim todas as
    medições do trabalho partem das mesmas condições. Na C2 (atualização), o item de teste é criado uma vez antes
    da medição, já que não há mais aquecimento para fazer a inserção.
  - Quando o PostgreSQL ignora o índice criado (plano Seq Scan), a leitura é medida também com o uso
    do índice forçado (rótulo "(índice forçado)").

Uso (com os containers de pé: `docker compose up -d`), a partir da raiz do projeto:

    sudo -v                                      # autentica o sudo, no MESMO terminal
    python benchmark.py                          # os 3 experimentos (volume em Pequeno/Médio/Grande)
    python benchmark.py volume                   # só o experimento de volume
    python benchmark.py volume --presets Pequeno # só um volume (bom para testar rápido)
    python benchmark.py volume --consultas C2,C3,C8   # refaz só essas consultas e mantém o resto do JSON
    python benchmark.py carga                    # só a carga dos 3 volumes (tempo, CPU, memória, espaço): poucos minutos
    python benchmark.py seletividade lote --preset Médio
    python benchmark.py --sem-so                 # sem sudo: limpa só o cache dos bancos
                                                 # (o do sistema operacional continua quente: documente)

A carga inicial de cada volume é repetida 5 vezes (--repeats-carga), recriando o banco do zero a cada rodada;
resultados_carga.json guarda a última rodada e resultados_carga_rodadas.json guarda todas.

Por padrão grava em ./resultados/ (resultados_volume.json, resultados_carga.json,
resultados_seletividade.json e resultados_lote.json). O resultados_carga.json guarda, para cada volume,
o tempo, a CPU, a memória e o espaço em disco da carga inicial dos dois bancos. Para acompanhar CPU e memória, rode o monitorar.py em outro terminal.
"""
import argparse
import gc
import hashlib
import json
import os
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path

import pandas as pd
from bson import ObjectId
from dotenv import load_dotenv
from faker import Faker
from psycopg2.extras import execute_values
from pymongo import MongoClient
from sqlalchemy import create_engine, event, text

RAIZ = Path(__file__).resolve().parent

# ----------------------------------------------------------------------------
# 1. Configuração do ambiente
# ----------------------------------------------------------------------------
load_dotenv(RAIZ / "catalogo-postgres" / ".env")
load_dotenv(RAIZ / "catalogo-mongo" / ".env")

pg_user = os.getenv("PGUSER")
pg_password = os.getenv("PGPASSWORD")
pg_db = os.getenv("PGDATABASE")
pg_host = os.getenv("PGHOST")
pg_port = os.getenv("PGPORT")

engine_pg = create_engine(f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}")

# Quando True, o PostgreSQL é impedido de escolher varredura sequencial (força o uso do índice criado).
# Só é ligado, temporariamente, para medir o índice que o planejador ignorou
FORCAR_INDICE_PG = False

# Quando True, as consultas pelo id são obrigadas a varrer a tabela inteira: no PostgreSQL desliga as varreduras
# por índice na sessão; no MongoDB usa hint({$natural: 1}). Os dois bancos sempre têm esse índice (chave primária
# / _id, que não pode ser removido), então "sem índice" aqui significa "sem USAR o índice".
SEM_INDICE = False


@event.listens_for(engine_pg, "checkout")
def _aplicar_forcar_indice(dbapi_conn, conexao_registro, conexao_proxy):
    # o pool reutiliza conexões: reaplica a configuração a cada uso, para valer sempre o estado atual
    cursor = dbapi_conn.cursor()
    cursor.execute(f"SET enable_seqscan = {'off' if FORCAR_INDICE_PG else 'on'}")
    chave = "off" if SEM_INDICE else "on"
    cursor.execute(f"SET enable_indexscan = {chave}; SET enable_bitmapscan = {chave}; SET enable_indexonlyscan = {chave}")
    cursor.close()
    dbapi_conn.commit()

mongo_uri = os.getenv("MONGO_URI")
mongo_db_name = os.getenv("MONGO_DB")

# journal=True: o MongoDB só confirma a escrita após o journal ir ao disco, equivalente ao COMMIT
# do PostgreSQL (synchronous_commit=on), para a comparação de escrita (C2, C8, lote) ser justa
client_mongo = MongoClient(mongo_uri, w=1, journal=True)
db_mongo = client_mongo[mongo_db_name]

def dica_mongo():
    return {"hint": [("$natural", 1)]} if SEM_INDICE else {}


@contextmanager
def varredura_forcada():
    global SEM_INDICE
    SEM_INDICE = True
    try:
        yield
    finally:
        SEM_INDICE = False


def sem_indice(acao):
    # a mesma ação, mas varrendo a tabela inteira em vez de usar o índice do id
    def executar(*args, **kwargs):
        with varredura_forcada():
            return acao(*args, **kwargs)
    return executar


random.seed(42)  # gera as mesmas coisas todas as vezes
fake = Faker("pt_BR")
Faker.seed(42)

# ----------------------------------------------------------------------------
# 2. Schema
# ----------------------------------------------------------------------------
DDL_POSTGRES = """
DROP TABLE IF EXISTS itens CASCADE;
DROP TABLE IF EXISTS restaurantes CASCADE;

CREATE TABLE restaurantes (
  id          UUID DEFAULT gen_random_uuid(),
  nome        VARCHAR(255) NOT NULL,
  categoria   VARCHAR(100),
  ativo       BOOLEAN NOT NULL DEFAULT true,
  criado_em   TIMESTAMP NOT NULL DEFAULT now(),

  CONSTRAINT pk_restaurante PRIMARY KEY (id)
);

CREATE TABLE itens (
  id                    UUID DEFAULT gen_random_uuid(),
  restaurante_id        UUID NOT NULL,
  nome                  VARCHAR(255) NOT NULL,
  categoria             VARCHAR(100) NOT NULL,
  status                VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE',
  preco                 JSONB NOT NULL,
  opcoes_grupos         JSONB,
  atributos_variaveis   JSONB NOT NULL DEFAULT '{}'::jsonb,
  hash                  INT,
  versao                BIGINT NOT NULL DEFAULT 1,
  criado_em             TIMESTAMP NOT NULL DEFAULT now(),
  modificado_em         TIMESTAMP NOT NULL DEFAULT now(),

  CONSTRAINT pk_item PRIMARY KEY (id),
  CONSTRAINT fk_item_restaurante FOREIGN KEY (restaurante_id)
    REFERENCES restaurantes(id) ON DELETE CASCADE
);
"""

def resetar_schema():
    with engine_pg.begin() as conn:
        conn.execute(text(DDL_POSTGRES))
    print("Tabelas do PostgreSQL recriadas.")

    db_mongo.restaurantes.drop()
    db_mongo.itens.drop()
    print("Coleções do MongoDB limpas.")

# ----------------------------------------------------------------------------
# 3. Geração de dados
# ----------------------------------------------------------------------------
PRESETS = {
    "Pequeno": (50, 5_000),
    "Médio": (200, 100_000),
    "Grande": (1000, 1_000_000),
}
NUM_RESTAURANTES, NUM_ITENS = PRESETS["Grande"]

restaurantes = None
itens = None

import hashlib

CATEGORIAS_REST = ['italiana', 'brasileira', 'árabe', 'mexicana']
CATEGORIAS_ITEM = ['pizza', 'bebida', 'sobremesa', 'lanche']
PESOS_CATEGORIA_ITEM = [0.35, 0.20, 0.10, 0.35]  # pizza, bebida, sobremesa, lanche

PREFIXOS_POR_CATEGORIA_REST = {
    'italiana': ['Cantina', 'Trattoria', 'Pizzaria'],
    'brasileira': ['Sabor', 'Empório', 'Fogão de Chão'],
    'árabe': ['Oásis', 'Empório Árabe', 'Casa Árabe'],
    'mexicana': ['Cantina Mexicana', 'Casa do Taco', 'Sabor Mexicano'],
}

NOMES_POR_CATEGORIA = {
    'pizza': ['Margherita', 'Calabresa', 'Quatro Queijos', 'Frango com Catupiry', 'Portuguesa',
              'Napolitana', 'Vegetariana', 'Pepperoni'],
    'bebida': ['Coca-Cola Lata', 'Guaraná Antarctica', 'Suco de Laranja', 'Água Mineral',
               'Suco de Uva', 'Chá Gelado', 'Água com Gás'],
    'sobremesa': ['Petit Gateau', 'Pudim', 'Mousse de Maracujá', 'Brownie',
                  'Sorvete', 'Torta de Limão', 'Cheesecake'],
    'lanche': ['X-Burger', 'X-Salada', 'X-Bacon', 'X-Tudo', 'X-Frango', 'Wrap de Frango'],
}

COMPLEMENTOS_ITEM = ['Tradicional', 'Especial', 'da Casa', 'Artesanal', 'Premium', 'Clássico', None]

TAGS = ['vegetariano', 'vegano', 'mais vendido', 'sem glúten', 'sem lactose', 'picante', 'novidade']
INGREDIENTES = ['queijo', 'bacon', 'alface', 'tomate', 'ovo', 'molho especial',
                 'cebola caramelizada', 'picles', 'maionese temperada', 'cheddar']

# PORTE DO RESTAURANTE — controla quantos itens cada um vai ter, de propósito
# desigual, para permitir testar seletividade na C4 (restaurante pequeno vs. grande)
PORTES_RESTAURANTE = ["pequeno", "medio", "grande"]
PESOS_PORTE = [0.60, 0.30, 0.10]          # 60% pequenos, 30% médios, 10% grandes
PESO_ITENS_POR_PORTE = {"pequeno": 1, "medio": 5, "grande": 20}  # peso relativo na distribuição de itens

def sortear_tags():
    return [t for t in TAGS if random.random() > 0.7]

def sortear_ingredientes():
    escolhidos = [i for i in INGREDIENTES if random.random() > 0.5]
    return escolhidos if len(escolhidos) >= 2 else INGREDIENTES[:2]

def gerar_promocao():
    return {"descontoPercentual": random.randint(5, 30), "validoAte": "2026-12-31"}

# ATRIBUTOS_VARIAVEIS — só características descritivas, sem sobreposição com opcoes_grupos
def gerar_atributos(categoria):
    if categoria == 'pizza':
        attrs = {}
        tags = sortear_tags()
        if tags:
            attrs["tags"] = tags
        if random.random() > 0.6:
            attrs["promocao"] = gerar_promocao()
        return attrs
    if categoria == 'bebida':
        attrs = {"gelada": random.random() > 0.2}
        tags = sortear_tags()
        if tags:
            attrs["tags"] = tags
        return attrs
    if categoria == 'sobremesa':
        attrs = {"informacaoNutricional": {"calorias": random.randint(200, 500),
                                             "contemGluten": random.random() > 0.5}}
        if random.random() > 0.7:
            attrs["promocao"] = gerar_promocao()
        return attrs
    # lanche
    attrs = {"ingredientes": sortear_ingredientes()}
    tags = sortear_tags()
    if tags:
        attrs["tags"] = tags
    if random.random() > 0.65:
        attrs["promocao"] = gerar_promocao()
    return attrs

# OPCOES_GRUPOS — escolhas configuráveis pelo cliente
# Para lanche, a oferta de "Bacon extra"/"Cheddar extra" é decidida pelo
# RESTAURANTE (não pelo item), garantindo consistência entre os itens de
# um mesmo estabelecimento.
def gerar_opcoes_grupos(categoria, restaurante):
    if categoria == 'pizza':
        return [
            {"nome": "Borda", "minimo": 0, "maximo": 1,
             "opcoes": [
                 {"nome": "Sem borda", "precoAdicional": 0.0},
                 {"nome": "Borda mussarela", "precoAdicional": 8.0},
                 {"nome": "Borda catupiry", "precoAdicional": 10.0},
             ]},
            {"nome": "Tamanho", "minimo": 1, "maximo": 1,
             "opcoes": [
                 {"nome": "Média", "precoAdicional": 0.0},
                 {"nome": "Grande", "precoAdicional": 12.0},
                 {"nome": "Família", "precoAdicional": 20.0},
             ]},
        ]
    if categoria == 'lanche':
        opcoes_adicionais = [{"nome": "Sem cebola", "precoAdicional": 0.0}]
        if restaurante["oferece_bacon_extra"]:
            opcoes_adicionais.append({"nome": "Bacon extra", "precoAdicional": 3.0})
        if restaurante["oferece_cheddar_extra"]:
            opcoes_adicionais.append({"nome": "Cheddar extra", "precoAdicional": 3.5})
        return [
            {"nome": "Adicionais", "minimo": 0, "maximo": 5, "opcoes": opcoes_adicionais},
        ]
    if categoria == 'bebida':
        return [
            {"nome": "Tamanho", "minimo": 1, "maximo": 1,
             "opcoes": [
                 {"nome": "350ml", "precoAdicional": 0.0},
                 {"nome": "500ml", "precoAdicional": 2.0},
                 {"nome": "600ml", "precoAdicional": 3.5},
             ]},
        ]
    # sobremesa
    return [
        {"nome": "Acompanhamento", "minimo": 0, "maximo": 2,
         "opcoes": [
             {"nome": "Calda", "precoAdicional": 2.0},
             {"nome": "Sem acompanhamento", "precoAdicional": 0.0},
         ]},
    ]

# PRECO como objeto JSON
# precoOriginal só é preenchido quando o item tem promocao (derivado do descontoPercentual)
def gerar_preco_json(categoria, atributos):
    faixas = {'pizza': (35, 70), 'bebida': (5, 15), 'sobremesa': (12, 25), 'lanche': (18, 35)}
    lo, hi = faixas[categoria]
    valor = round(random.uniform(lo, hi), 2)
    promocao = atributos.get("promocao")
    preco_original = round(valor / (1 - promocao["descontoPercentual"] / 100), 2) if promocao else None
    return {"valor": valor, "moeda": "BRL", "precoOriginal": preco_original}

# STATUS (substitui disponivel)
def gerar_status():
    return random.choices(
        ["AVAILABLE", "UNAVAILABLE", "DELETED"],
        weights=[85, 10, 5],
        k=1
    )[0]

# HASH — "impressão digital" do conteúdo do item
def calcular_hash(nome, categoria, status, preco, opcoes_grupos, atributos):
    conteudo = json.dumps(
        {"nome": nome, "categoria": categoria, "status": status,
         "preco": preco, "opcoes_grupos": opcoes_grupos, "atributos": atributos},
        sort_keys=True, ensure_ascii=False
    )
    digest = hashlib.md5(conteudo.encode("utf-8")).hexdigest()
    valor = int(digest[:8], 16)
    if valor >= 2**31:
        valor -= 2**32
    return valor

def gerar_nome_restaurante(categoria, usados):
    prefixo = random.choice(PREFIXOS_POR_CATEGORIA_REST[categoria])
    for _ in range(10):
        complemento = random.choice([fake.last_name(), fake.city()])
        nome = f"{prefixo} {complemento}"
        if nome not in usados:
            return nome
    return f"{prefixo} {fake.unique.last_name()}"

def gerar_nome_item(categoria):
    base = random.choice(NOMES_POR_CATEGORIA[categoria])
    complemento = random.choice(COMPLEMENTOS_ITEM)
    return f"{base} {complemento}" if complemento else base

def gerar_dados(num_restaurantes, num_itens):
    global restaurantes, itens, NUM_RESTAURANTES, NUM_ITENS
    NUM_RESTAURANTES, NUM_ITENS = num_restaurantes, num_itens
    random.seed(42)  # garante que restaurantes/itens iniciais sejam os mesmos a cada volume
    Faker.seed(42)

    restaurantes = []
    nomes_restaurantes_usados = set()
    for i in range(num_restaurantes):
        categoria = CATEGORIAS_REST[i % len(CATEGORIAS_REST)]
        nome = gerar_nome_restaurante(categoria, nomes_restaurantes_usados)
        nomes_restaurantes_usados.add(nome)
        porte = random.choices(PORTES_RESTAURANTE, weights=PESOS_PORTE, k=1)[0]
        restaurantes.append({
            "pg_id": str(uuid.uuid4()),
            "mongo_id": ObjectId(),
            "nome": nome,
            "categoria": categoria,
            "ativo": True,
            "oferece_bacon_extra": random.random() > 0.4,     # ~60% dos restaurantes oferecem
            "oferece_cheddar_extra": random.random() > 0.75,  # ~25% dos restaurantes oferecem
            "porte": porte,                                   # usado para distribuir itens de forma desigual
        })

    # Garante explicitamente um restaurante bem pequeno e um bem grande,
    # para servir como casos de teste de seletividade na C4 — sem depender
    # de sorteio aleatório para achar um exemplo de cada extremo
    restaurantes[0]["porte"] = "pequeno"
    restaurantes[1]["porte"] = "grande"

    # Peso de distribuição de itens por restaurante, proporcional ao porte
    pesos_distribuicao_itens = [PESO_ITENS_POR_PORTE[r["porte"]] for r in restaurantes]

    itens = []
    for i in range(num_itens):
        restaurante = random.choices(restaurantes, weights=pesos_distribuicao_itens, k=1)[0]
        categoria = random.choices(CATEGORIAS_ITEM, weights=PESOS_CATEGORIA_ITEM, k=1)[0]

        nome = gerar_nome_item(categoria)
        status = gerar_status()
        atributos = gerar_atributos(categoria)
        preco = gerar_preco_json(categoria, atributos)
        opcoes_grupos = gerar_opcoes_grupos(categoria, restaurante)
        hash_item = calcular_hash(nome, categoria, status, preco, opcoes_grupos, atributos)

        itens.append({
            "restaurante": restaurante,
            "nome": nome,
            "categoria": categoria,
            "status": status,
            "preco": preco,
            "opcoes_grupos": opcoes_grupos,
            "atributos": atributos,
            "hash": hash_item,
            "versao": 1,
        })

    print(f"Gerados: {len(restaurantes)} restaurantes e {len(itens)} itens.")
    print(f"Restaurante de teste PEQUENO: {restaurantes[0]['nome']}")
    print(f"Restaurante de teste GRANDE: {restaurantes[1]['nome']}")

# ----------------------------------------------------------------------------
# 4. Medição de tempo
# ----------------------------------------------------------------------------
import time
import statistics

def medir_tempo(acao, repeats=5, warm_up=True, antes=None):
    # `antes` (opcional) roda antes de CADA execução, fora da cronometragem
    # (ex.: restaurar o estado do banco). Sem ele, o comportamento é o de sempre.
    try:
        if warm_up:
            if antes:
                antes()
            acao()  # descarta a primeira execução

        tempos = []
        for _ in range(repeats):
            if antes:
                antes()
            inicio = time.perf_counter()
            acao()
            tempos.append((time.perf_counter() - inicio) * 1000)  # em ms

        return {
            "min": min(tempos),
            "max": max(tempos),
            "media": statistics.mean(tempos),
            "mediana": statistics.median(tempos),
            "tempos_brutos": tempos,
        }
    except Exception as e:
        print("Erro ao executar benchmark:", e)
        return None

# ----------------------------------------------------------------------------
# 5. Inserção dos dados
# ----------------------------------------------------------------------------
# Diferente do notebook, a conversão para o formato de cada banco é feita lote a lote
# (em vez de materializar uma cópia completa dos dados), para reduzir o pico de memória.
# Nada disso entra em medição na Seção 7, então o resultado é o mesmo.
TAMANHO_LOTE = 10_000  # mesmo tamanho de lote para PostgreSQL e MongoDB

# A conversão dos dados para o formato de cada banco (json.dumps no PostgreSQL, dicts no MongoDB) roda no
# Python e não deve entrar na comparação. Só o tempo gasto ENVIANDO ao banco é contado (tempo_envio), e a
# CPU/memória dos containers só é amostrada enquanto há envio em andamento (como o notebook, que
# convertia tudo antes de medir).
_enviando = threading.Event()
_tempo_envio_s = [0.0]


class _Envio:
    def __enter__(self):
        self.inicio = time.perf_counter()
        _enviando.set()

    def __exit__(self, *_):
        _tempo_envio_s[0] += time.perf_counter() - self.inicio
        _enviando.clear()


def inserir_postgres():
    conn = engine_pg.raw_connection()
    try:
        with conn.cursor() as cur:
            dados_restaurantes = [(r["pg_id"], r["nome"], r["categoria"], r["ativo"]) for r in restaurantes]
            with _Envio():
                execute_values(
                    cur,
                    "INSERT INTO restaurantes (id, nome, categoria, ativo) VALUES %s",
                    dados_restaurantes,
                    page_size=TAMANHO_LOTE,
                )
            for i in range(0, len(itens), TAMANHO_LOTE):
                rows = [
                    (
                        it["restaurante"]["pg_id"],
                        it["nome"],
                        it["categoria"],
                        it["status"],
                        json.dumps(it["preco"], ensure_ascii=False),
                        json.dumps(it["opcoes_grupos"], ensure_ascii=False),
                        json.dumps(it["atributos"], ensure_ascii=False),
                        it["hash"],
                        it["versao"],
                    )
                    for it in itens[i:i + TAMANHO_LOTE]
                ]
                with _Envio():
                    execute_values(
                        cur,
                        "INSERT INTO itens (restaurante_id, nome, categoria, status, preco, opcoes_grupos, atributos_variaveis, hash, versao) VALUES %s",
                        rows,
                        template="(%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)",
                        page_size=TAMANHO_LOTE,
                    )
        with _Envio():
            conn.commit()  # tudo em uma única transação, como no notebook
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inserir_mongo():
    agora = datetime.now()  # equivale ao DEFAULT now() do PostgreSQL
    docs_restaurantes = [
        {
            "_id": r["mongo_id"],
            "nome": r["nome"],
            "categoria": r["categoria"],
            "ativo": r["ativo"],
            "criado_em": agora,
        }
        for r in restaurantes
    ]
    with _Envio():
        db_mongo.restaurantes.insert_many(docs_restaurantes)
    for i in range(0, len(itens), TAMANHO_LOTE):
        docs = []
        for it in itens[i:i + TAMANHO_LOTE]:
            doc = {
                "restauranteId": it["restaurante"]["mongo_id"],
                "nome": it["nome"],
                "categoria": it["categoria"],
                "status": it["status"],
                "preco": it["preco"],
                "opcoes_grupos": it["opcoes_grupos"],
                "hash": it["hash"],
                "versao": it["versao"],
                "criado_em": agora,
                "modificado_em": agora,
            }
            doc.update(it["atributos"])  # adiciona os campos descritivos direto no documento
            docs.append(doc)
        with _Envio():
            db_mongo.itens.insert_many(docs)


PAUSA_POS_CARGA_S = 10  # segundos de espera depois da carga, antes da primeira medição


def estabilizar_apos_carga():
    # Depois de uma carga grande os bancos ainda gravam em disco em segundo plano (no teste com
    # 1 milhão de itens o disco ficou saturado ~35 s), o que atrapalharia as primeiras medições.
    # Força a gravação agora e espera o disco acalmar
    with engine_pg.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("CHECKPOINT"))
    client_mongo.admin.command("fsync")
    time.sleep(PAUSA_POS_CARGA_S)


def analisar_postgres():
    # Atualiza as estatísticas das tabelas logo depois da carga, para que todos os cenários (com e sem
    # índice) partam das mesmas estatísticas, em vez de depender de quando o autovacuum rodaria sozinho.
    # (O ANALYZE feito ao criar cada índice continua: ele é necessário para o índice de expressão do C5)
    with engine_pg.begin() as conn:
        conn.execute(text("ANALYZE restaurantes"))
        conn.execute(text("ANALYZE itens"))


# --- Medição da carga: tempo, CPU, memória e espaço ---------------------------
UNIDADES_MIB = {"B": 1 / 1048576, "kB": 1 / 1024, "KiB": 1 / 1024, "MB": 0.953674, "MiB": 1.0,
                "GB": 976.5625, "GiB": 1024.0, "TB": 1_000_000.0, "TiB": 1_048_576.0}


def para_mib(texto):
    # "284.1MiB" / "1.2GiB" -> MiB (o notebook não convertia GiB)
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", texto)
    return float(m.group(1)) * UNIDADES_MIB.get(m.group(2), 1.0)


def capturar_recursos_docker(nome_container):
    saida = subprocess.run(
        ["docker", "stats", nome_container, "--no-stream", "--format", "{{.CPUPerc}},{{.MemUsage}}"],
        capture_output=True, text=True, check=True).stdout.strip()
    cpu_txt, mem_txt = saida.split(",")
    return float(cpu_txt.replace("%", "")), para_mib(mem_txt.split("/")[0])  # CPU em % (100% = 1 núcleo), memória em MiB


def caminho_cgroup(nome_container):
    # cgroup v2 do container: leitura direta dos contadores, instantânea (o `docker stats` leva ~1 s por chamada
    # e perderia as cargas curtas). Devolve None se não achar: aí usa o `docker stats`
    try:
        cid = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", nome_container],
                             capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None
    for base in (Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope"), Path(f"/sys/fs/cgroup/docker/{cid}")):
        if (base / "cpu.stat").exists() and (base / "memory.current").exists():
            return base
    return None


def ler_cgroup(base):
    # (CPU acumulada em segundos, memória em MiB); a memória descarta o cache de arquivos, como o `docker stats`
    uso = next(int(l.split()[1]) for l in (base / "cpu.stat").read_text().splitlines() if l.startswith("usage_usec"))
    inativo = next(int(l.split()[1]) for l in (base / "memory.stat").read_text().splitlines()
                   if l.startswith("inactive_file"))
    memoria = int((base / "memory.current").read_text()) - inativo
    return uso / 1e6, memoria / 1048576


def monitorar_recursos(nome_container, amostras, parar, intervalo):
    base = caminho_cgroup(nome_container)
    if base is None:
        intervalo = max(intervalo, 1.0)
    else:
        intervalo = min(intervalo, 0.25)
        cpu_ant, _ = ler_cgroup(base)
        t_ant = time.perf_counter()
    while not parar.is_set():
        durante_envio = _enviando.is_set()
        try:
            if base is None:
                cpu, mem = capturar_recursos_docker(nome_container)
            else:
                parar.wait(intervalo)
                cpu_s, mem = ler_cgroup(base)
                agora = time.perf_counter()
                cpu = (cpu_s - cpu_ant) / (agora - t_ant) * 100  # % de um núcleo, como o `docker stats`
                cpu_ant, t_ant = cpu_s, agora
            if durante_envio or _enviando.is_set():  # só vale a amostra tirada com envio em andamento
                amostras.append((cpu, mem))
        except Exception:
            pass
        if base is None:
            parar.wait(intervalo)


def medir_insercao_com_recursos(funcao_insercao, nome_container, intervalo=0.25):
    amostras = []
    parar = threading.Event()
    monitor = threading.Thread(target=monitorar_recursos, args=(nome_container, amostras, parar, intervalo))
    _tempo_envio_s[0] = 0.0
    monitor.start()
    inicio = time.perf_counter()
    try:
        funcao_insercao()
    finally:
        tempo_total_ms = (time.perf_counter() - inicio) * 1000
        parar.set()
        monitor.join()
    cpus = [a[0] for a in amostras]
    memorias = [a[1] for a in amostras]
    return {
        "tempo_envio_ms": _tempo_envio_s[0] * 1000,  # só o envio ao banco (sem a conversão no Python)
        "tempo_total_ms": tempo_total_ms,            # inclui a conversão dos dados lote a lote
        "cpu_media_percent": statistics.mean(cpus) if cpus else None,
        "cpu_max_percent": max(cpus) if cpus else None,
        "memoria_media_mb": statistics.mean(memorias) if memorias else None,
        "memoria_max_mb": max(memorias) if memorias else None,
        "num_amostras": len(amostras),
        "cpus_brutos": cpus,
        "memorias_brutas": memorias,
    }


def medir_espaco_dados_pg(nome_tabela):
    with engine_pg.connect() as conn:
        # heap + TOAST, sem os índices
        return conn.execute(text(f"SELECT pg_table_size('{nome_tabela}')")).scalar() / 1024 / 1024  # MB


def medir_espaco_dados_mongo(nome_colecao):
    client_mongo.admin.command("fsync")  # força checkpoint: sem isso o storageSize fica defasado logo após a inserção
    return db_mongo.command("collStats", nome_colecao)["storageSize"] / 1024 / 1024  # em disco, comprimido (MB)


def medir_tamanho_logico_mongo(nome_colecao):
    # tamanho dos dados sem compressão (BSON): comparável ao PostgreSQL, que não comprime a tabela
    return db_mongo.command("collStats", nome_colecao)["size"] / 1024 / 1024  # MB


def preparar_ambiente(num_restaurantes, num_itens, estabilizar=True):
    """Recria o schema, gera os dados e carrega os dois bancos. Devolve as métricas da carga de cada um
    (tempo, CPU, memória e espaço), que o experimento de volume grava em resultados_carga.json."""
    global itens
    resetar_schema()
    gerar_dados(num_restaurantes, num_itens)
    carga = {"num_restaurantes": num_restaurantes, "num_itens": num_itens}
    carga["PostgreSQL"] = medir_insercao_com_recursos(inserir_postgres, "catalogo-postgres-db")
    carga["PostgreSQL"]["espaco_dados_mb"] = medir_espaco_dados_pg("itens")
    carga["MongoDB"] = medir_insercao_com_recursos(inserir_mongo, "catalogo-mongo-db")
    carga["MongoDB"]["espaco_dados_mb"] = medir_espaco_dados_mongo("itens")
    carga["MongoDB"]["tamanho_logico_mb"] = medir_tamanho_logico_mongo("itens")
    analisar_postgres()
    if estabilizar:
        estabilizar_apos_carga()
    # Os itens gerados já estão nos dois bancos e nenhum experimento da Seção 7 os usa depois:
    # libera a memória (só `restaurantes` continua necessário)
    itens = None
    gc.collect()
    return carga

# ----------------------------------------------------------------------------
# 6. Consultas (somente as definições; sem execução)
# ----------------------------------------------------------------------------
def extrair_nome_indice(plano):
    plano_texto = str(plano)
    for chave in ["'Index Name': '", "'indexName': '"]:
        if chave in plano_texto:
            inicio = plano_texto.find(chave) + len(chave)
            fim = plano_texto.find("'", inicio)
            return plano_texto[inicio:fim]
    return "nenhum"

def encontrar_estagio_scan(plano_stage):
    if plano_stage.get("stage") in ("COLLSCAN", "IXSCAN"):
        return plano_stage.get("stage")   # achou, para aqui e devolve
    if "inputStage" in plano_stage:
        return encontrar_estagio_scan(plano_stage["inputStage"])  # não achou, tenta de novo, uma camada mais fundo
    return plano_stage.get("stage")  # não tem mais camadas, devolve o que tiver

def apagar_indices_pg(nome_tabela="itens"):
    with engine_pg.begin() as conn:
        indices_existentes = conn.execute(text(
            f"SELECT indexname FROM pg_indexes WHERE tablename = '{nome_tabela}' AND indexname NOT LIKE 'pk_%'"
        )).fetchall()
        for (nome_indice,) in indices_existentes:
            conn.execute(text(f"DROP INDEX IF EXISTS {nome_indice}"))
    print(f"Índices removidos: {[i[0] for i in indices_existentes] if indices_existentes else 'nenhum'}")

# Estado de teste preenchido pelos preparar_* abaixo (no notebook vinha das células de execução)
restaurante_teste_c2 = id_teste_c2_pg = contador_c2_pg = eventos_c2_pg = contador_evento_pg = None
restaurante_teste_c2_mongo = id_teste_c2_mongo = contador_c2_mongo = None
eventos_c2_mongo = contador_evento_mongo = None
item_id_teste = item_id_teste_mongo = None
snapshot_c3_pg = snapshot_c3_mongo = snapshot_c8_pg = snapshot_c8_mongo = None

# deletados=True troca o filtro `status <> 'DELETED'` por `status = 'DELETED'`: uma consulta bem mais
# seletiva (~5% dos itens são DELETED), usada na seletividade para ver onde o índice passa a ajudar
def consulta_c1_pg(categoria="pizza", deletados=False):
    operador = "=" if deletados else "<>"
    return pd.read_sql(
        text(f"SELECT nome, categoria, status FROM itens WHERE categoria = :categoria AND status {operador} 'DELETED'"),
        engine_pg,
        params={"categoria": categoria}
    )

def explain_c1_pg(categoria="pizza", deletados=False):
    operador = "=" if deletados else "<>"
    with engine_pg.connect() as conn:
        resultado = conn.execute(text(
            "EXPLAIN (ANALYZE, FORMAT JSON) SELECT nome, categoria, status FROM itens "
            f"WHERE categoria = :categoria AND status {operador} 'DELETED'"
        ), {"categoria": categoria}).scalar()
        return resultado[0]

# serve apenas para conferir o resultado, e também é usada para medir o tempo
def consulta_c1_mongo(categoria="pizza", deletados=False):
    cursor = db_mongo.itens.find(
        {"categoria": categoria, "status": "DELETED" if deletados else {"$ne": "DELETED"}},
        {"_id": 0, "nome": 1, "categoria": 1, "status": 1}
    )
    return pd.DataFrame(list(cursor))

# usada uma vez, só pra confirmar visualmente o plano de execução
def explain_c1_mongo(categoria="pizza", deletados=False):
    return db_mongo.command(
        "explain",
        {
            "find": "itens",
            "filter": {"categoria": categoria, "status": "DELETED" if deletados else {"$ne": "DELETED"}},
            "projection": {"_id": 0, "nome": 1, "categoria": 1, "status": 1},
        },
        verbosity="executionStats"
    )

import uuid as uuid_lib

def gerar_evento_c2_pg(id_item=None, versao=None):
    # sem argumentos: a cada chamada, simula um "evento novo" do item de teste, com versão
    # maior e conteúdo diferente, garantindo que a condição de update (hash <> e versao >=)
    # seja sempre satisfeita. Com id_item/versao: usado pelo teste de lote (vários itens)
    if versao is None:
        contador_c2_pg["versao"] += 1
        versao = contador_c2_pg["versao"]
    categoria = "lanche"
    nome = f"Item Teste C2 v{versao}"
    status = "AVAILABLE"
    atributos = gerar_atributos(categoria)
    preco = gerar_preco_json(categoria, atributos)
    opcoes_grupos = gerar_opcoes_grupos(categoria, restaurante_teste_c2)
    hash_novo = calcular_hash(nome, categoria, status, preco, opcoes_grupos, atributos)
    return {
        "id": id_item if id_item is not None else id_teste_c2_pg,
        "restaurante_id": restaurante_teste_c2["pg_id"],
        "nome": nome,
        "categoria": categoria,
        "status": status,
        "preco": json.dumps(preco, ensure_ascii=False),
        "opcoes_grupos": json.dumps(opcoes_grupos, ensure_ascii=False),
        "atributos": json.dumps(atributos, ensure_ascii=False),
        "hash": hash_novo,
        "versao": versao,
    }

# C2 no PostgreSQL: upsert SEM `ON CONFLICT`. O `INSERT ... ON CONFLICT (id) DO UPDATE` seria a forma mais eficiente e
# idiomática, mas ele sempre usa o índice da chave primária por dentro para detectar o conflito, e os parâmetros do
# planejador (enable_indexscan e afins) não o desligam. Como a C2 também é medida "sem usar o índice" (C2_id, para a
# análise do efeito do índice), a mesma instrução precisa poder buscar o id por índice ou por varredura. Por isso a C2 usa
# em TODOS os experimentos (consulta, volume, índice e lote) um UPDATE condicionado seguido de INSERT se o item não
# existe: a mesma lógica "ler, decidir, escrever" que o MongoDB já usa.
SQL_UPSERT_C2_PG = """
    WITH atualizado AS (
        UPDATE itens
        SET nome = :nome, categoria = :categoria, status = :status, preco = CAST(:preco AS JSONB),
            opcoes_grupos = CAST(:opcoes_grupos AS JSONB), atributos_variaveis = CAST(:atributos AS JSONB),
            hash = :hash, versao = :versao, modificado_em = now()
        WHERE id = CAST(:id AS UUID) AND hash <> :hash AND :versao >= versao
        RETURNING id
    )
    INSERT INTO itens (id, restaurante_id, nome, categoria, status, preco, opcoes_grupos, atributos_variaveis, hash, versao)
    SELECT CAST(:id AS UUID), CAST(:restaurante_id AS UUID), :nome, :categoria, :status, CAST(:preco AS JSONB),
           CAST(:opcoes_grupos AS JSONB), CAST(:atributos AS JSONB), :hash, :versao
    WHERE NOT EXISTS (SELECT 1 FROM itens WHERE id = CAST(:id AS UUID))
"""

def upsert_c2_pg(evento):
    with engine_pg.begin() as conn:
        conn.execute(text(SQL_UPSERT_C2_PG), evento)

def acao_c2_pg():
    upsert_c2_pg(next(contador_evento_pg))

def acao_c2_insercao_pg():
    upsert_c2_pg(next(eventos_insercao_c2_pg))

def gerar_evento_c2_mongo(id_item=None, versao=None):
    if versao is None:
        contador_c2_mongo["versao"] += 1
        versao = contador_c2_mongo["versao"]
    categoria = "lanche"
    nome = f"Item Teste C2 v{versao}"
    status = "AVAILABLE"
    atributos = gerar_atributos(categoria)
    preco = gerar_preco_json(categoria, atributos)
    opcoes_grupos = gerar_opcoes_grupos(categoria, restaurante_teste_c2_mongo)
    hash_novo = calcular_hash(nome, categoria, status, preco, opcoes_grupos, atributos)
    agora = datetime.now()
    doc = {
        "_id": id_item if id_item is not None else id_teste_c2_mongo,
        "restauranteId": restaurante_teste_c2_mongo["mongo_id"],
        "nome": nome,
        "categoria": categoria,
        "status": status,
        "preco": preco,
        "opcoes_grupos": opcoes_grupos,
        "hash": hash_novo,
        "versao": versao,
        "criado_em": agora,
        "modificado_em": agora,
    }
    doc.update(atributos)
    return doc

def upsert_c2_mongo(evento):
    """
    A checagem de hash/versão precisa ser feita na aplicação: primeiro
    BUSCA o documento existente, e só depois decide se escreve — o
    MongoDB não tem um operador nativo equivalente ao WHERE hash <> do Postgres.
    """
    existente = db_mongo.itens.find_one({"_id": evento["_id"]}, **dica_mongo())

    if existente is None:
        db_mongo.itens.insert_one(evento)
    elif existente["hash"] != evento["hash"] and evento["versao"] >= existente["versao"]:
        evento["criado_em"] = existente["criado_em"]  # replace não pode apagar a data de criação
        db_mongo.itens.replace_one({"_id": evento["_id"]}, evento, **dica_mongo())

def acao_c2_mongo():
    upsert_c2_mongo(next(contador_evento_mongo))

def acao_c2_insercao_mongo():
    upsert_c2_mongo(next(eventos_insercao_c2_mongo))

from psycopg2.extras import Json, execute_values

from pymongo import UpdateOne

# Snapshot/restauração dos valores originais de um conjunto de itens no PostgreSQL.
# Usado no `antes` do medir_tempo: permite que a escrita meça com COMMIT de verdade e
# que cada repetição parta do mesmo estado, sem entrar na cronometragem
def tirar_snapshot_pg(ids, colunas):
    with engine_pg.connect() as conn:
        return conn.execute(text(
            f"SELECT id::text, {', '.join(colunas)} FROM itens WHERE id = ANY(CAST(:ids AS uuid[]))"
        ), {"ids": [str(i) for i in ids]}).fetchall()

def restaurar_snapshot_pg(snapshot, colunas, jsonb=()):
    if not snapshot:
        return
    template = "(%s::uuid, " + ", ".join("%s::jsonb" if c in jsonb else "%s" for c in colunas) + ")"
    linhas = [(r[0], *[Json(v) if c in jsonb and v is not None else v for c, v in zip(colunas, r[1:])])
              for r in snapshot]
    sql = (f"UPDATE itens SET {', '.join(f'{c} = v.{c}' for c in colunas)} "
           f"FROM (VALUES %s) AS v(id, {', '.join(colunas)}) WHERE itens.id = v.id")
    conn = engine_pg.raw_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, sql, linhas, template=template, page_size=len(linhas))  # uma única instrução
        conn.commit()
    finally:
        conn.close()

# Mesma ideia no MongoDB
def tirar_snapshot_mongo(ids, campos):
    return {d["_id"]: d for d in db_mongo.itens.find({"_id": {"$in": ids}}, {c: 1 for c in campos})}

def restaurar_snapshot_mongo(snapshot, campos):
    ops = []
    for _id, doc in snapshot.items():
        update = {}
        presentes = {c: doc[c] for c in campos if c in doc}
        ausentes = {c: "" for c in campos if c not in doc}
        if presentes:
            update["$set"] = presentes
        if ausentes:
            update["$unset"] = ausentes
        if update:
            ops.append(UpdateOne({"_id": _id}, update))
    if ops:
        db_mongo.itens.bulk_write(ops, ordered=False)

def consulta_c3_pg():
    return pd.read_sql(
        text("SELECT id, status FROM itens WHERE id = :id"),
        engine_pg,
        params={"id": item_id_teste}
    )

def acao_c3_pg(ids=None):
    # sem argumento: o item de teste; com uma lista de ids: delete lógico em lote
    if ids is None:
        ids = [item_id_teste]
    # A mudança de status não é idempotente: o estado original é restaurado pelo `antes`
    # do medir_tempo, fora da cronometragem. A transação faz COMMIT (como no C2), então o
    # custo de gravar o log entra na medição
    with engine_pg.begin() as conn:
        conn.execute(text(
            "UPDATE itens SET status = 'DELETED', hash = 0, modificado_em = now() "
            "WHERE id = ANY(CAST(:ids AS uuid[])) AND status <> 'DELETED'"
        ), {"ids": [str(i) for i in ids]})

COLUNAS_C3_PG = ["status", "hash", "modificado_em"]

restaurar_c3_pg = lambda: restaurar_snapshot_pg(snapshot_c3_pg, COLUNAS_C3_PG)

from datetime import datetime

def consulta_c3_mongo():
    return db_mongo.itens.find_one({"_id": item_id_teste_mongo}, {"_id": 0, "status": 1})

COLUNAS_C3_MONGO = ["status", "hash", "modificado_em"]

restaurar_c3_mongo = lambda: restaurar_snapshot_mongo(snapshot_c3_mongo, COLUNAS_C3_MONGO)

def acao_c3_mongo(ids=None):
    # sem argumento: o item de teste; com uma lista de ids: delete lógico em lote.
    # O estado original é restaurado pelo `antes` do medir_tempo, fora da cronometragem,
    # garantindo que cada repetição meça uma transição real (AVAILABLE → DELETED)
    if ids is None:
        ids = [item_id_teste_mongo]
    db_mongo.itens.update_many(
        {"_id": {"$in": ids}, "status": {"$ne": "DELETED"}},
        {"$set": {"status": "DELETED", "hash": 0, "modificado_em": datetime.now()}},
        **dica_mongo()
    )

def consulta_c4_pg(nome_restaurante_teste=None):
    if nome_restaurante_teste is None:
        nome_restaurante_teste = restaurantes[0]["nome"]
    return pd.read_sql(
        text(
            "SELECT i.nome, i.categoria, i.status, r.nome AS restaurante_nome "
            "FROM itens i JOIN restaurantes r ON r.id = i.restaurante_id "
            "WHERE r.nome = :nome AND i.status <> 'DELETED'"
        ),
        engine_pg,
        params={"nome": nome_restaurante_teste}
    )

def explain_c4_pg(nome_restaurante_teste=None):
    if nome_restaurante_teste is None:
        nome_restaurante_teste = restaurantes[0]["nome"]
    with engine_pg.connect() as conn:
        resultado = conn.execute(text(
            "EXPLAIN (ANALYZE, FORMAT JSON) "
            "SELECT i.nome, i.categoria, i.status, r.nome AS restaurante_nome "
            "FROM itens i JOIN restaurantes r ON r.id = i.restaurante_id "
            "WHERE r.nome = :nome AND i.status <> 'DELETED'"
        ), {"nome": nome_restaurante_teste}).scalar()
        return resultado[0]

def consulta_c4_mongo(nome_restaurante_teste=None):
    if nome_restaurante_teste is None:
        nome_restaurante_teste = restaurantes[0]["nome"]
    restaurante = db_mongo.restaurantes.find_one({"nome": nome_restaurante_teste})
    cursor = db_mongo.itens.find(
        {"restauranteId": restaurante["_id"], "status": {"$ne": "DELETED"}},
        {"_id": 0, "nome": 1, "categoria": 1, "status": 1}
    )
    return pd.DataFrame(list(cursor))

def explain_c4_mongo(nome_restaurante_teste=None):
    if nome_restaurante_teste is None:
        nome_restaurante_teste = restaurantes[0]["nome"]
    restaurante = db_mongo.restaurantes.find_one({"nome": nome_restaurante_teste})
    return db_mongo.command(
        "explain",
        {
            "find": "itens",
            "filter": {"restauranteId": restaurante["_id"], "status": {"$ne": "DELETED"}},
            "projection": {"_id": 0, "nome": 1, "categoria": 1, "status": 1},
        },
        verbosity="executionStats"
    )

def consulta_c5_pg(minimo=20, maximo=50):
    return pd.read_sql(
        text(
            "SELECT nome, categoria, preco FROM itens "
            "WHERE (preco->>'valor')::numeric BETWEEN :minimo AND :maximo AND status <> 'DELETED'"
        ),
        engine_pg,
        params={"minimo": minimo, "maximo": maximo}
    )

def explain_c5_pg(minimo=20, maximo=50):
    with engine_pg.connect() as conn:
        resultado = conn.execute(text(
            "EXPLAIN (ANALYZE, FORMAT JSON) SELECT nome, categoria, preco FROM itens "
            "WHERE (preco->>'valor')::numeric BETWEEN :minimo AND :maximo AND status <> 'DELETED'"
        ), {"minimo": minimo, "maximo": maximo}).scalar()
        return resultado[0]

def consulta_c5_mongo(minimo=20, maximo=50):
    cursor = db_mongo.itens.find(
        {"preco.valor": {"$gte": minimo, "$lte": maximo}, "status": {"$ne": "DELETED"}},
        {"_id": 0, "nome": 1, "categoria": 1, "preco": 1}
    )
    return pd.DataFrame(list(cursor))

def explain_c5_mongo(minimo=20, maximo=50):
    return db_mongo.command(
        "explain",
        {
            "find": "itens",
            "filter": {"preco.valor": {"$gte": minimo, "$lte": maximo}, "status": {"$ne": "DELETED"}},
            "projection": {"_id": 0, "nome": 1, "categoria": 1, "preco": 1},
        },
        verbosity="executionStats"
    )

def consulta_c6_pg():
    return pd.read_sql(
        "SELECT nome, categoria, atributos_variaveis->'promocao' AS promocao FROM itens "
        "WHERE atributos_variaveis ? 'promocao' AND status <> 'DELETED'",
        engine_pg
    )

def explain_c6_pg():
    with engine_pg.connect() as conn:
        resultado = conn.execute(text(
            "EXPLAIN (ANALYZE, FORMAT JSON) SELECT nome, categoria, atributos_variaveis->'promocao' AS promocao FROM itens "
            "WHERE atributos_variaveis ? 'promocao' AND status <> 'DELETED'"
        )).scalar()
        return resultado[0]

def consulta_c6_mongo():
    cursor = db_mongo.itens.find(
        {"promocao": {"$exists": True}, "status": {"$ne": "DELETED"}},
        {"_id": 0, "nome": 1, "categoria": 1, "promocao": 1}
    )
    return pd.DataFrame(list(cursor))

def explain_c6_mongo():
    return db_mongo.command(
        "explain",
        {
            "find": "itens",
            "filter": {"promocao": {"$exists": True}, "status": {"$ne": "DELETED"}},
            "projection": {"_id": 0, "nome": 1, "categoria": 1, "promocao": 1},
        },
        verbosity="executionStats"
    )

def consulta_c7_pg(opcao="Bacon extra"):
    return pd.read_sql(
        text(
            "SELECT nome, categoria, opcoes_grupos FROM itens "
            "WHERE opcoes_grupos @> CAST(:filtro AS jsonb) "
            "AND status <> 'DELETED'"
        ),
        engine_pg,
        params={"filtro": json.dumps([{"opcoes": [{"nome": opcao}]}])}
    )

def explain_c7_pg(opcao="Bacon extra"):
    with engine_pg.connect() as conn:
        resultado = conn.execute(text(
            "EXPLAIN (ANALYZE, FORMAT JSON) SELECT nome, categoria, opcoes_grupos FROM itens "
            "WHERE opcoes_grupos @> CAST(:filtro AS jsonb) "
            "AND status <> 'DELETED'"
        ), {"filtro": json.dumps([{"opcoes": [{"nome": opcao}]}])}).scalar()
        return resultado[0]

def consulta_c7_mongo(opcao="Bacon extra"):
    cursor = db_mongo.itens.find(
        {"opcoes_grupos.opcoes.nome": opcao, "status": {"$ne": "DELETED"}},
        {"_id": 0, "nome": 1, "categoria": 1, "opcoes_grupos": 1}
    )
    return pd.DataFrame(list(cursor))

def explain_c7_mongo(opcao="Bacon extra"):
    return db_mongo.command(
        "explain",
        {
            "find": "itens",
            "filter": {"opcoes_grupos.opcoes.nome": opcao, "status": {"$ne": "DELETED"}},
            "projection": {"_id": 0, "nome": 1, "categoria": 1, "opcoes_grupos": 1},
        },
        verbosity="executionStats"
    )

COLUNAS_C8_PG = ["atributos_variaveis", "modificado_em"]

restaurar_c8_pg = lambda: restaurar_snapshot_pg(snapshot_c8_pg, COLUNAS_C8_PG, jsonb=("atributos_variaveis",))

def consulta_c8_pg():
    return pd.read_sql(
        text("SELECT id, atributos_variaveis FROM itens WHERE id = :id"),
        engine_pg,
        params={"id": item_id_teste}
    )

SQL_UPDATE_C8_PG = """
    UPDATE itens
    SET atributos_variaveis = jsonb_set(
          atributos_variaveis, '{tags}',
          COALESCE(atributos_variaveis->'tags', '[]'::jsonb) || '"novidade"'::jsonb
        ),
        modificado_em = now()
    WHERE id = ANY(CAST(:ids AS uuid[]))
      AND NOT COALESCE(atributos_variaveis->'tags', '[]'::jsonb) @> '"novidade"'::jsonb
"""

def acao_c8_pg(ids=None):
    # sem argumento: o item de teste; com uma lista de ids: atualização em lote
    if ids is None:
        ids = [item_id_teste]
    # A concatenação de JSONB não é idempotente: o NOT ... @> do WHERE evita duplicar a tag
    # (equivale ao $addToSet) e o estado original é restaurado pelo `antes`
    # do medir_tempo, fora da cronometragem. A transação faz COMMIT (como no C2), então o
    # custo de gravar o log entra na medição
    with engine_pg.begin() as conn:
        conn.execute(text(SQL_UPDATE_C8_PG), {"ids": [str(i) for i in ids]})

COLUNAS_C8_MONGO = ["tags", "modificado_em"]

restaurar_c8_mongo = lambda: restaurar_snapshot_mongo(snapshot_c8_mongo, COLUNAS_C8_MONGO)

def consulta_c8_mongo():
    return db_mongo.itens.find_one({"_id": item_id_teste_mongo}, {"_id": 0, "tags": 1})

def acao_c8_mongo(ids=None):
    # sem argumento: o item de teste; com uma lista de ids: atualização em lote
    if ids is None:
        ids = [item_id_teste_mongo]
    # $addToSet é idempotente: depois da 1ª execução a tag já existe e o array não muda mais.
    # Por isso o estado original é restaurado pelo `antes` do medir_tempo, fora da cronometragem
    db_mongo.itens.update_many(
        {"_id": {"$in": ids}, "tags": {"$ne": "novidade"}},  # só toca quem ainda não tem a tag (como no PostgreSQL)
        {"$addToSet": {"tags": "novidade"}, "$set": {"modificado_em": datetime.now()}},
        **dica_mongo()
    )

# ----------------------------------------------------------------------------
# 7. Escalabilidade
# ----------------------------------------------------------------------------
# Cada preparar_* garante um estado limpo (remove qualquer índice antes de
# recriar o seu), independente da ordem em que as consultas rodem no loop.

def preparar_c1_pg_sem_indice():
    apagar_indices_pg()

def preparar_c1_pg_btree():
    apagar_indices_pg()
    with engine_pg.begin() as conn:
        conn.execute(text("CREATE INDEX btree_c1 ON itens (categoria, status)"))
        conn.execute(text("ANALYZE itens"))

def preparar_c1_mongo_sem_indice():
    db_mongo.itens.drop_indexes()

def preparar_c1_mongo_btree():
    db_mongo.itens.drop_indexes()
    db_mongo.itens.create_index([("categoria", 1), ("status", 1)], name="btree_c1")


def preparar_c5_pg_sem_indice():
    apagar_indices_pg()

def preparar_c5_pg_btree():
    apagar_indices_pg()
    with engine_pg.begin() as conn:
        conn.execute(text("CREATE INDEX btree_c5 ON itens (((preco->>'valor')::numeric))"))
        conn.execute(text("ANALYZE itens"))

def preparar_c5_mongo_sem_indice():
    db_mongo.itens.drop_indexes()

def preparar_c5_mongo_indice():
    db_mongo.itens.drop_indexes()
    db_mongo.itens.create_index([("preco.valor", 1)], name="single_field_c5")


def preparar_c6_pg_sem_indice():
    apagar_indices_pg()

def preparar_c6_pg_gin():
    apagar_indices_pg()
    with engine_pg.begin() as conn:
        conn.execute(text("CREATE INDEX gin_c6 ON itens USING GIN (atributos_variaveis)"))
        conn.execute(text("ANALYZE itens"))

def preparar_c6_mongo_sem_indice():
    db_mongo.itens.drop_indexes()

def preparar_c6_mongo_wildcard():
    db_mongo.itens.drop_indexes()
    db_mongo.itens.create_index({"$**": 1}, name="wildcard_c6")


def preparar_c4_pg_sem_indice():
    apagar_indices_pg()

def preparar_c4_pg_btree():
    apagar_indices_pg()
    with engine_pg.begin() as conn:
        conn.execute(text("CREATE INDEX btree_c4 ON itens (restaurante_id, status)"))
        conn.execute(text("ANALYZE itens"))

def preparar_c4_mongo_sem_indice():
    db_mongo.itens.drop_indexes()

def preparar_c4_mongo_btree():
    db_mongo.itens.drop_indexes()
    db_mongo.itens.create_index([("restauranteId", 1), ("status", 1)], name="btree_c4")


def preparar_c7_pg_sem_indice():
    apagar_indices_pg()

def preparar_c7_pg_gin():
    apagar_indices_pg()
    with engine_pg.begin() as conn:
        conn.execute(text("CREATE INDEX gin_c7 ON itens USING GIN (opcoes_grupos)"))
        conn.execute(text("ANALYZE itens"))

def preparar_c7_mongo_sem_indice():
    db_mongo.itens.drop_indexes()

def preparar_c7_mongo_multikey():
    db_mongo.itens.drop_indexes()
    db_mongo.itens.create_index([("opcoes_grupos.opcoes.nome", 1)], name="multikey_c7")


def preparar_c2_pg():
    # gera um novo lote de 11 eventos (1 aquecimento + 10 repeats) a cada
    # chamada, porque acao_c2_pg consome um iterador de uso único
    global restaurante_teste_c2, id_teste_c2_pg, contador_c2_pg
    global eventos_c2_pg, contador_evento_pg
    apagar_indices_pg()
    restaurante_teste_c2 = restaurantes[0]
    id_teste_c2_pg = str(uuid_lib.uuid4())
    contador_c2_pg = {"versao": 0}
    eventos_c2_pg = [gerar_evento_c2_pg() for _ in range(11)]
    contador_evento_pg = iter(eventos_c2_pg)

def preparar_c2_mongo():
    global restaurante_teste_c2_mongo, id_teste_c2_mongo, contador_c2_mongo
    global eventos_c2_mongo, contador_evento_mongo
    db_mongo.itens.drop_indexes()
    restaurante_teste_c2_mongo = restaurantes[0]
    id_teste_c2_mongo = ObjectId()
    contador_c2_mongo = {"versao": 0}
    eventos_c2_mongo = [gerar_evento_c2_mongo() for _ in range(11)]
    contador_evento_mongo = iter(eventos_c2_mongo)


def preparar_c8_pg_sem_indice():
    global item_id_teste, snapshot_c8_pg
    apagar_indices_pg()
    with engine_pg.connect() as conn:
        item_id_teste = conn.execute(text("SELECT id FROM itens LIMIT 1")).scalar()
    snapshot_c8_pg = tirar_snapshot_pg([item_id_teste], COLUNAS_C8_PG)  # usado por restaurar_c8_pg

def preparar_c8_mongo_sem_indice():
    global item_id_teste_mongo, snapshot_c8_mongo
    db_mongo.itens.drop_indexes()
    item_id_teste_mongo = db_mongo.itens.find_one({})["_id"]
    snapshot_c8_mongo = tirar_snapshot_mongo([item_id_teste_mongo], COLUNAS_C8_MONGO)  # usado por restaurar_c8_mongo

def preparar_c3_pg():
    global item_id_teste, snapshot_c3_pg
    apagar_indices_pg()
    with engine_pg.connect() as conn:
        item_id_teste = conn.execute(
            text("SELECT id FROM itens WHERE status <> 'DELETED' LIMIT 1")
        ).scalar()
    snapshot_c3_pg = tirar_snapshot_pg([item_id_teste], COLUNAS_C3_PG)  # usado por restaurar_c3_pg

def preparar_c3_mongo():
    global item_id_teste_mongo, snapshot_c3_mongo
    db_mongo.itens.drop_indexes()
    item_id_teste_mongo = db_mongo.itens.find_one({"status": {"$ne": "DELETED"}})["_id"]
    snapshot_c3_mongo = tirar_snapshot_mongo([item_id_teste_mongo], COLUNAS_C3_MONGO)  # usado por restaurar_c3_mongo

CENARIOS_ESCALABILIDADE = {
    "C1": [
        {"label": "PostgreSQL - Sem índice", "preparar": preparar_c1_pg_sem_indice, "acao": consulta_c1_pg},
        {"label": "PostgreSQL - Índice B-Tree", "preparar": preparar_c1_pg_btree, "acao": consulta_c1_pg},
        {"label": "MongoDB - Sem índice", "preparar": preparar_c1_mongo_sem_indice, "acao": consulta_c1_mongo},
        {"label": "MongoDB - Índice B-Tree", "preparar": preparar_c1_mongo_btree, "acao": consulta_c1_mongo},
    ],
    "C5": [
        {"label": "PostgreSQL - Sem índice", "preparar": preparar_c5_pg_sem_indice, "acao": consulta_c5_pg},
        {"label": "PostgreSQL - Índice B-Tree", "preparar": preparar_c5_pg_btree, "acao": consulta_c5_pg},
        {"label": "MongoDB - Sem índice", "preparar": preparar_c5_mongo_sem_indice, "acao": consulta_c5_mongo},
        {"label": "MongoDB - Índice Single-field", "preparar": preparar_c5_mongo_indice, "acao": consulta_c5_mongo},
    ],
    "C6": [
        {"label": "PostgreSQL - Sem índice", "preparar": preparar_c6_pg_sem_indice, "acao": consulta_c6_pg},
        {"label": "PostgreSQL - Índice GIN", "preparar": preparar_c6_pg_gin, "acao": consulta_c6_pg},
        {"label": "MongoDB - Sem índice", "preparar": preparar_c6_mongo_sem_indice, "acao": consulta_c6_mongo},
        {"label": "MongoDB - Índice Wildcard", "preparar": preparar_c6_mongo_wildcard, "acao": consulta_c6_mongo},
    ],
    "C4": [
        {"label": "PostgreSQL - Sem índice", "preparar": preparar_c4_pg_sem_indice, "acao": consulta_c4_pg},
        {"label": "PostgreSQL - Índice B-Tree", "preparar": preparar_c4_pg_btree, "acao": consulta_c4_pg},
        {"label": "MongoDB - Sem índice", "preparar": preparar_c4_mongo_sem_indice, "acao": consulta_c4_mongo},
        {"label": "MongoDB - Índice B-Tree", "preparar": preparar_c4_mongo_btree, "acao": consulta_c4_mongo},
    ],
    "C7": [
        {"label": "PostgreSQL - Sem índice", "preparar": preparar_c7_pg_sem_indice, "acao": consulta_c7_pg},
        {"label": "PostgreSQL - Índice GIN", "preparar": preparar_c7_pg_gin, "acao": consulta_c7_pg},
        {"label": "MongoDB - Sem índice", "preparar": preparar_c7_mongo_sem_indice, "acao": consulta_c7_mongo},
        {"label": "MongoDB - Índice Multikey", "preparar": preparar_c7_mongo_multikey, "acao": consulta_c7_mongo},
    ],
    "C2": [
        {"label": "PostgreSQL", "preparar": preparar_c2_pg, "acao": acao_c2_pg},
        {"label": "MongoDB", "preparar": preparar_c2_mongo, "acao": acao_c2_mongo},
    ],
    "C8": [
        {"label": "PostgreSQL", "preparar": preparar_c8_pg_sem_indice, "acao": acao_c8_pg, "antes": restaurar_c8_pg},
        {"label": "MongoDB", "preparar": preparar_c8_mongo_sem_indice, "acao": acao_c8_mongo, "antes": restaurar_c8_mongo},
    ],
    "C3": [
        {"label": "PostgreSQL", "preparar": preparar_c3_pg, "acao": acao_c3_pg, "antes": restaurar_c3_pg},
        {"label": "MongoDB", "preparar": preparar_c3_mongo, "acao": acao_c3_mongo, "antes": restaurar_c3_mongo},
    ],
}

# ----------------------------------------------------------------------------
# O índice do id nas escritas C2, C3 e C8
# ----------------------------------------------------------------------------
# O id é o caso mais seletivo possível (1 linha). Aqui C2, C3 e C8 são medidas com o índice do id (chave primária /
# _id) e obrigando a varredura da tabela inteira ("Sem índice"), para comparar com as consultas de leitura. Os dois
# bancos sempre têm esse índice e ele não pode ser removido: "sem índice" quer dizer "sem USAR o índice"
# (PostgreSQL: varreduras por índice desligadas na sessão; MongoDB: hint $natural). Ficam em consultas à parte
# (C2_id, C3_id, C8_id) para não se misturarem com as escritas normais dos gráficos de escrita e de lote.
#
# C2: o PostgreSQL usa o mesmo upsert sem ON CONFLICT da C2 normal (ver SQL_UPSERT_C2_PG), nos dois cenários.

def criar_explain_id(id_pg, id_mongo):
    # plano de acesso do WHERE id = ...: o mesmo para a leitura e para a escrita que localiza o item pelo id
    def explain_pg():
        with engine_pg.connect() as conn:
            return conn.execute(text(
                "EXPLAIN (ANALYZE, FORMAT JSON) SELECT nome, categoria, status FROM itens WHERE id = CAST(:id AS UUID)"
            ), {"id": str(id_pg())}).scalar()[0]

    def explain_mongo():
        comando = {"find": "itens", "filter": {"_id": id_mongo()}, "projection": {"_id": 0, "nome": 1}}
        comando.update({"hint": {"$natural": 1}} if SEM_INDICE else {})
        return db_mongo.command("explain", comando, verbosity="executionStats")

    return explain_pg, explain_mongo

explain_c2_id = criar_explain_id(lambda: id_teste_c2_pg, lambda: id_teste_c2_mongo)
explain_c3_id = criar_explain_id(lambda: item_id_teste, lambda: item_id_teste_mongo)
explain_c8_id = explain_c3_id

def _par(consulta, preparar_pg, acao_pg, preparar_mongo, acao_mongo, antes_pg=None, antes_mongo=None):
    cenarios = []
    for nome, preparar, acao, antes, rotulo_com in (
            ("PostgreSQL", preparar_pg, acao_pg, antes_pg, "PostgreSQL - Índice (chave primária)"),
            ("MongoDB", preparar_mongo, acao_mongo, antes_mongo, "MongoDB - Índice (_id)")):
        base = {"preparar": preparar}
        if antes:
            base["antes"] = antes
        cenarios.append({"label": rotulo_com, "acao": acao, **base})
        cenarios.append({"label": f"{nome} - Sem índice", "acao": sem_indice(acao), **base})
    return cenarios

CENARIOS_ID = {
    "C2_id": _par("C2", preparar_c2_pg, acao_c2_pg, preparar_c2_mongo, acao_c2_mongo),  # find_one e replace_one respeitam o hint de varredura
    "C3_id": _par("C3", preparar_c3_pg, acao_c3_pg, preparar_c3_mongo, acao_c3_mongo, restaurar_c3_pg, restaurar_c3_mongo),
    "C8_id": _par("C8", preparar_c8_pg_sem_indice, acao_c8_pg, preparar_c8_mongo_sem_indice, acao_c8_mongo,
                  restaurar_c8_pg, restaurar_c8_mongo),
}

# --- Volume total do banco ---------------------------------------------------
def reiniciar_fases(saida):
    # começa um fases.csv novo a cada rodada (ele só acrescenta linhas, então misturaria rodadas antigas)
    (saida / "fases.csv").unlink(missing_ok=True)


def marcar(saida, fase):
    # registra o início de cada fase (usado por monitorar.py para marcar o gráfico)
    saida.mkdir(parents=True, exist_ok=True)
    novo = not (saida / "fases.csv").exists()
    with open(saida / "fases.csv", "a", encoding="utf-8") as f:
        if novo:
            f.write("epoch,fase\n")
        f.write(f"{time.time():.2f},{fase}\n")


def carregar_json_existente(caminho):
    # usado com --consultas: refaz só algumas consultas e mantém o resto do arquivo como estava
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}


def salvar_json(dados, caminho):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# --- Descrição do cenário: índice usado, tipo de acesso e espaço do índice ---
def medir_espaco_indice_pg(nome_tabela):
    with engine_pg.connect() as conn:
        # soma só os índices criados pelo teste: exclui a chave primária, que existe em todos os cenários
        tamanho_indices = conn.execute(text(
            f"SELECT COALESCE(SUM(pg_relation_size(indexrelid)), 0)::bigint "
            f"FROM pg_index WHERE indrelid = '{nome_tabela}'::regclass AND NOT indisprimary"
        )).scalar()
    return tamanho_indices / 1024 / 1024  # bytes -> MB


def medir_espaco_indice_mongo(nome_colecao):
    client_mongo.admin.command("fsync")  # força checkpoint: sem isso o tamanho do índice fica defasado
    stats = db_mongo.command("collStats", nome_colecao)
    # totalIndexSize inclui o índice automático _id_ (equivalente à chave primária); subtrai para medir só os índices do teste
    return (stats["totalIndexSize"] - stats["indexSizes"].get("_id_", 0)) / 1024 / 1024  # em MB


# (explain no PostgreSQL, explain no MongoDB) de cada consulta que tem cenário com/sem índice
EXPLAINS = {
    "C1": (explain_c1_pg, explain_c1_mongo),
    "C4": (explain_c4_pg, explain_c4_mongo),
    "C5": (explain_c5_pg, explain_c5_mongo),
    "C6": (explain_c6_pg, explain_c6_mongo),
    "C7": (explain_c7_pg, explain_c7_mongo),
    "C2_id": explain_c2_id,
    "C3_id": explain_c3_id,
    "C8_id": explain_c8_id,
}


def no_varredura_itens_pg(plano):
    # percorre o plano e devolve o nó que lê a tabela itens (em C4 há também a varredura de restaurantes)
    achados = []

    def visitar(no):
        if no.get("Relation Name") == "itens" and "Scan" in no.get("Node Type", ""):
            achados.append(no)
        for filho in no.get("Plans", []):
            visitar(filho)

    visitar(plano["Plan"])
    return achados[0] if achados else plano["Plan"]


def descrever_cenario(nome_consulta, label, argumentos=None):
    """Índice que o plano usou, tipo de acesso e espaço ocupado pelos índices (fora da cronometragem).
    `argumentos`: parâmetros da consulta (como em `chamar`); sem eles usa os valores padrão."""
    eh_pg = label.startswith("PostgreSQL")
    chave = nome_consulta.split(" - ")[0]  # "C2 - Inserção" -> "C2"
    if chave in EXPLAINS:
        explain = EXPLAINS[chave][0 if eh_pg else 1]
        forcar = chave.endswith("_id") and "Sem índice" in label  # o plano também precisa da varredura forçada
        with (varredura_forcada() if forcar else nullcontext()):
            plano = explain() if argumentos is None else chamar(explain, argumentos)
        if eh_pg:
            no = no_varredura_itens_pg(plano)
            acesso = no["Node Type"]
            if no.get("Parallel Aware") and acesso.startswith("Seq"):
                acesso = "Parallel " + acesso
            indice = extrair_nome_indice(no)
        else:
            vencedor = plano["queryPlanner"]["winningPlan"]  # só o plano vencedor (não os rejeitados)
            acesso = encontrar_estagio_scan(vencedor)
            indice = extrair_nome_indice(vencedor)
    else:
        indice, acesso = "apenas chave primária", None  # C2, C3 e C8: acesso por chave, sem índice de teste
    espaco = (medir_espaco_indice_pg if eh_pg else medir_espaco_indice_mongo)("itens")
    return {"indice": indice, "tipo_acesso": acesso, "espaco_indice_mb": espaco}


def imprimir_resultado(rotulo, r, info=None, extra=""):
    if r is None:
        print(f"{rotulo}: erro na medição")
        return
    print(f"{rotulo}: min {r['min']:.2f} | max {r['max']:.2f} | "
          f"média {r['media']:.2f} | mediana {r['mediana']:.2f} ms{extra}")
    if info:
        acesso = f" | acesso: {info['tipo_acesso']}" if info["tipo_acesso"] else ""
        print(f"    índice: {info['indice']}{acesso} | espaço do índice: {info['espaco_indice_mb']:.2f} MB")


def medir_forcado_se_ignorado(nome_consulta, label, info, acao, repeats, argumentos=None, **kwargs_medir):
    """Se o PostgreSQL ignorou o índice criado (plano Seq Scan), repete a medição forçando o uso do índice.
    Devolve (resultado, info) da variante forçada, ou None quando não se aplica (sem índice no cenário,
    Mongo, escritas, ou o planejador já usou o índice)."""
    global FORCAR_INDICE_PG
    if not label.startswith("PostgreSQL") or "Sem índice" in label or info["indice"] != "nenhum":
        return None
    FORCAR_INDICE_PG = True
    try:
        info_forcado = descrever_cenario(nome_consulta, label, argumentos)  # plano com a varredura sequencial desligada
        resultado = medir_tempo(acao, repeats=repeats, **kwargs_medir)
    finally:
        FORCAR_INDICE_PG = False
    if resultado is not None:
        resultado.update(info_forcado)
    return resultado, info_forcado


SUFIXO_FORCADO = " (índice forçado)"


# --- Limpeza de cache ---------------------------------------------------------
CONTAINERS = ["catalogo-postgres-db", "catalogo-mongo-db"]
TEMPO_MAXIMO_ESPERA = 120  # s para os bancos voltarem depois de reiniciar
usar_sudo = True


# Script de root que só descarta o cache do sistema operacional. Se existir (e o sudoers o liberar sem
# senha), é ele que é usado: funciona também no kernel do Jupyter. Senão, usa `sudo -n sh -c ...`
SCRIPT_LIMPAR_SO = "/usr/local/bin/limpar_cache_so"


def comando_limpar_so():
    if Path(SCRIPT_LIMPAR_SO).exists():
        return ["sudo", "-n", SCRIPT_LIMPAR_SO]
    return ["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"]


def checar_sudo():
    # com o script dedicado, a regra do sudoers só vale para ele: a checagem é executá-lo (inofensivo)
    comando = comando_limpar_so() if Path(SCRIPT_LIMPAR_SO).exists() else ["sudo", "-n", "true"]
    if subprocess.run(comando, capture_output=True).returncode != 0:
        sys.exit("sudo exige senha. Rode `sudo -v` antes, no mesmo terminal (e deixe valer), ou use --sem-so "
                 "(limpa só o cache dos bancos, não o do sistema operacional).")


def esperar_bancos():
    limite = time.time() + TEMPO_MAXIMO_ESPERA
    while time.time() < limite:
        try:
            with engine_pg.connect() as conn:
                conn.execute(text("SELECT 1"))
            client_mongo.admin.command("ping")
            return
        except Exception:
            time.sleep(1)
    sys.exit("Os bancos não voltaram a tempo depois do restart.")


def encadear(*funcoes):
    # junta funções (ignora as None) numa só, para o `antes` do medir_tempo: ex. restaurar o estado e limpar o cache
    def executar():
        for f in funcoes:
            if f:
                f()
    return executar


def limpar_cache():
    # roda no `antes` do medir_tempo: fora da cronometragem
    subprocess.run(["docker", "stop", *CONTAINERS], capture_output=True, check=True)
    if usar_sudo:
        subprocess.run(comando_limpar_so(), capture_output=True, check=True)
    subprocess.run(["docker", "start", *CONTAINERS], capture_output=True, check=True)
    engine_pg.dispose()  # descarta conexões do pool que morreram com o restart
    esperar_bancos()


# --- Volume total do banco -----------------------------------------------------
# C2, C3 e C8 "normais" só existem para o experimento de lote (que usa seus cenários para preparar o banco). No experimento de
# volume elas NÃO são medidas: a escrita pontual por chave já é medida em C2_id, C3_id e C8_id, com e sem índice, e o cenário
# "com índice" dessas é exatamente o mesmo comando das normais (medi-las de novo só repetiria o dado)
ESCRITAS_SO_NO_LOTE = {"C2", "C3", "C8"}
CONSULTAS_ESCRITA = {"C2", "C3", "C8", "C2_id", "C3_id", "C8_id"}  # escritas: não têm a variante "índice forçado", mas também são medidas com o cache limpo


def imprimir_carga(carga):
    for banco in ("PostgreSQL", "MongoDB"):
        c = carga[banco]
        cpu = f"{c['cpu_media_percent']:.0f}% (máx {c['cpu_max_percent']:.0f}%)" if c["num_amostras"] else "sem amostra"
        mem = f"{c['memoria_media_mb']:.0f} MB (máx {c['memoria_max_mb']:.0f} MB)" if c["num_amostras"] else "sem amostra"
        print(f"Carga {banco}: envio {c['tempo_envio_ms'] / 1000:.1f} s | CPU {cpu} | memória {mem} | "
              f"dados em disco {c['espaco_dados_mb']:.0f} MB | {c['num_amostras']} amostras")


def carregar_varias_vezes(nome_preset, num_rest, num_it, repeticoes):
    """Faz a carga inicial `repeticoes` vezes, recriando tabelas e coleções do zero a cada vez. Devolve a lista com a
    medição de cada rodada (tempo, CPU, memória e espaço). A última carga é a que fica no banco para os testes."""
    rodadas = []
    for n in range(1, repeticoes + 1):
        print(f"\n--- Carga {nome_preset}: rodada {n} de {repeticoes} ---")
        rodadas.append(preparar_ambiente(num_rest, num_it, estabilizar=False))  # o restart antes de cada repetição já grava tudo em disco
        imprimir_carga(rodadas[-1])
    return rodadas


def rodar_carga(presets, saida, repeticoes_carga=5):
    """Só a carga inicial de cada volume (tempo, CPU, memória e espaço), sem nenhuma consulta: leva poucos minutos.
    A carga é repetida `repeticoes_carga` vezes: resultados_carga.json guarda a última rodada de cada volume e
    resultados_carga_rodadas.json guarda todas, para o gráfico mostrar a média e a variação entre as rodadas."""
    caminho = saida / "resultados_carga.json"
    resultados_carga, rodadas_carga = {}, {}
    for nome_preset in presets:
        num_rest, num_it = PRESETS[nome_preset]
        print(f"\n=== Carga: {nome_preset} ({num_rest} restaurantes, {num_it} itens) ===")
        marcar(saida, f"carga {nome_preset}")
        rodadas_carga[nome_preset] = carregar_varias_vezes(nome_preset, num_rest, num_it, repeticoes_carga)
        resultados_carga[nome_preset] = rodadas_carga[nome_preset][-1]
        salvar_json(resultados_carga, caminho)
        salvar_json(rodadas_carga, saida / "resultados_carga_rodadas.json")
    print(f"Resultados salvos em {caminho}.")


def rodar_volume(presets, saida, repeats, consultas=None, repeticoes_carga=5):
    caminho = saida / "resultados_volume.json"
    # {preset: {consulta: {cenario: resultado_medir_tempo}}}; com `consultas`, parte do arquivo existente
    resultados_volume = carregar_json_existente(caminho) if consultas else {}
    # {preset: {PostgreSQL/MongoDB: tempo, CPU, memória e espaço da carga}}
    resultados_carga = carregar_json_existente(saida / "resultados_carga.json") if consultas else {}
    rodadas_carga = carregar_json_existente(saida / "resultados_carga_rodadas.json") if consultas else {}

    for nome_preset in presets:
        num_rest, num_it = PRESETS[nome_preset]
        print(f"\n=== Volume: {nome_preset} ({num_rest} restaurantes, {num_it} itens) ===")
        marcar(saida, f"volume {nome_preset}: carga")
        if consultas:
            # refazendo só algumas consultas: uma carga só, e as cargas já medidas (resultados_carga*.json) são mantidas
            preparar_ambiente(num_rest, num_it, estabilizar=False)
        else:
            # a carga é repetida (cada rodada recria o banco do zero); a última fica no banco para as consultas
            rodadas_carga[nome_preset] = carregar_varias_vezes(nome_preset, num_rest, num_it, repeticoes_carga)
            resultados_carga[nome_preset] = rodadas_carga[nome_preset][-1]
            salvar_json(resultados_carga, saida / "resultados_carga.json")
            salvar_json(rodadas_carga, saida / "resultados_carga_rodadas.json")

        resultados_volume.setdefault(nome_preset, {})
        for nome_consulta, cenarios in {**CENARIOS_ESCALABILIDADE, **CENARIOS_ID}.items():
            if nome_consulta in ESCRITAS_SO_NO_LOTE:
                continue
            if consultas and nome_consulta not in consultas:
                continue
            resultados_volume[nome_preset][nome_consulta] = {}
            marcar(saida, f"volume {nome_preset}: {nome_consulta}")
            leitura = nome_consulta not in CONSULTAS_ESCRITA
            for cenario in cenarios:
                cenario["preparar"]()  # cria o índice do cenário (uma vez)
                info = descrever_cenario(nome_consulta, cenario["label"])
                if nome_consulta in ("C2", "C2_id"):
                    cenario["acao"]()  # cria o item de teste (fora da medição): a escrita medida é a atualização
                # cache limpo antes de cada repetição (depois de restaurar o estado, nas escritas); sem aquecimento,
                # que esquentaria o cache
                resultado = medir_tempo(cenario["acao"], repeats=repeats, warm_up=False,
                                        antes=encadear(cenario.get("antes"), limpar_cache))
                if resultado is not None:
                    resultado.update(info)
                resultados_volume[nome_preset][nome_consulta][cenario["label"]] = resultado
                imprimir_resultado(f"{nome_consulta} | {cenario['label']}", resultado, info)
                if leitura:  # se o PostgreSQL ignorou o índice, mede também forçando o uso (com o cache limpo)
                    forcado = medir_forcado_se_ignorado(nome_consulta, cenario["label"], info, cenario["acao"],
                                                        repeats, warm_up=False, antes=limpar_cache)
                    if forcado:
                        label_forcado = cenario["label"] + SUFIXO_FORCADO
                        resultados_volume[nome_preset][nome_consulta][label_forcado] = forcado[0]
                        imprimir_resultado(f"{nome_consulta} | {label_forcado}", forcado[0], forcado[1])

        salvar_json(resultados_volume, caminho)  # salva a cada volume concluído
        print(f"Resultados salvos em {caminho} (até o volume {nome_preset}).")


# --- Seletividade ------------------------------------------------------------
def restaurante_por_porte(porte):
    return next(r for r in restaurantes if r["porte"] == porte)

# Depois de gerar_dados(), pois C4 depende dos restaurantes gerados.
# Cada parâmetro é (rótulo, argumento(s) passados à consulta).
def montar_parametros_seletividade():
    return {
        "C1": [(cat, cat) for cat in ["sobremesa", "bebida", "pizza"]],
        "C5": [
            ("R$ 5–12", {"minimo": 5, "maximo": 12}),
            ("R$ 5–25", {"minimo": 5, "maximo": 25}),
            ("R$ 5–50", {"minimo": 5, "maximo": 50}),
            ("R$ 5–70", {"minimo": 5, "maximo": 70}),
        ],
        "C4": [
            ("pequeno", restaurantes[0]["nome"]),
            ("medio", restaurante_por_porte("medio")["nome"]),
            ("grande", restaurantes[1]["nome"]),
        ],
        "C7": [("Cheddar extra", "Cheddar extra"), ("Bacon extra", "Bacon extra")],
        "C6": [("com promoção", None)],  # consulta sem parâmetro: um único ponto (a seletividade é a da própria consulta)
    }

def chamar(acao, valor):
    # valor dict -> argumentos nomeados (C5); caso contrário, argumento posicional único
    if valor is None:
        return acao()
    return acao(**valor) if isinstance(valor, dict) else acao(valor)

def rodar_seletividade(preset, saida, repeats, consultas=None):
    caminho = saida / "resultados_seletividade.json"
    print(f"\n=== Seletividade: volume {preset} ===")
    marcar(saida, f"seletividade {preset}: carga")
    preparar_ambiente(*PRESETS[preset], estabilizar=False)  # o restart antes de cada repetição já grava tudo em disco
    parametros_seletividade = montar_parametros_seletividade()

    # {consulta: {cenario: {rotulo_parametro: resultado_medir_tempo}}}; com `consultas`, parte do arquivo existente
    resultados_seletividade = carregar_json_existente(caminho) if consultas else {}

    for nome_consulta, parametros in parametros_seletividade.items():
        if consultas and nome_consulta not in consultas:
            continue
        resultados_seletividade[nome_consulta] = {}
        marcar(saida, f"seletividade: {nome_consulta}")
        for cenario in CENARIOS_ESCALABILIDADE[nome_consulta]:
            cenario["preparar"]()  # índice criado uma vez por cenário
            resultados_seletividade[nome_consulta][cenario["label"]] = {}
            for rotulo, valor in parametros:
                info = descrever_cenario(nome_consulta, cenario["label"], valor)  # o plano pode mudar com o parâmetro
                # cache limpo antes de cada repetição; sem aquecimento
                resultado = medir_tempo(lambda: chamar(cenario["acao"], valor), repeats=repeats,
                                        warm_up=False, antes=limpar_cache)
                resultado["n_resultados"] = len(chamar(cenario["acao"], valor))
                resultado.update(info)
                resultados_seletividade[nome_consulta][cenario["label"]][rotulo] = resultado
                imprimir_resultado(f"{nome_consulta} | {cenario['label']} | {rotulo} ({resultado['n_resultados']} linhas)",
                                   resultado, info)
                forcado = medir_forcado_se_ignorado(nome_consulta, cenario["label"], info,
                                                    lambda: chamar(cenario["acao"], valor), repeats,
                                                    argumentos=valor, warm_up=False, antes=limpar_cache)
                if forcado:
                    label_forcado = cenario["label"] + SUFIXO_FORCADO
                    forcado[0]["n_resultados"] = resultado["n_resultados"]
                    resultados_seletividade[nome_consulta].setdefault(label_forcado, {})[rotulo] = forcado[0]
                    imprimir_resultado(f"{nome_consulta} | {label_forcado} | {rotulo} ({resultado['n_resultados']} linhas)",
                                       forcado[0], forcado[1])

    salvar_json(resultados_seletividade, caminho)
    print(f"Resultados salvos em {caminho}.")


# --- Lote de escrita ---------------------------------------------------------
from psycopg2.extras import execute_values
from pymongo import InsertOne, ReplaceOne

LOTES = [1, 10, 100, 1_000, 10_000, 50_000, 100_000]  # itens por operação

def amostrar_ids(maximo):
    with engine_pg.connect() as conn:
        ids_pg = [r[0] for r in conn.execute(text(
            "SELECT id::text FROM itens WHERE status <> 'DELETED' ORDER BY random() LIMIT :n"
        ), {"n": maximo})]
    ids_mongo = [d["_id"] for d in db_mongo.itens.aggregate([
        {"$match": {"status": {"$ne": "DELETED"}}},
        {"$sample": {"size": maximo}},
        {"$project": {"_id": 1}},
    ])]
    return {"PostgreSQL": ids_pg, "MongoDB": ids_mongo}

# Cada fábrica recebe (n, ids) e devolve (antes, acao, depois):
#   antes  -> roda antes de cada repetição, fora do tempo (prepara/restaura)
#   acao   -> a operação em lote, cronometrada
#   depois -> limpeza ao fim das repetições daquele tamanho de lote

# C2 — upsert em lote
# mesma lógica da C2 individual (sem ON CONFLICT), para N itens numa única instrução: UPDATE dos que já existem e mudaram
# (hash diferente e versão não mais antiga) e INSERT dos que não existem
SQL_UPSERT_LOTE_C2_PG = """
    WITH v(id, restaurante_id, nome, categoria, status, preco, opcoes_grupos, atributos_variaveis, hash, versao) AS (
        VALUES %s
    ),
    atualizado AS (
        UPDATE itens i
        SET nome = v.nome, categoria = v.categoria, status = v.status, preco = v.preco,
            opcoes_grupos = v.opcoes_grupos, atributos_variaveis = v.atributos_variaveis,
            hash = v.hash, versao = v.versao, modificado_em = now()
        FROM v
        WHERE i.id = v.id AND i.hash <> v.hash AND v.versao >= i.versao
        RETURNING i.id
    )
    INSERT INTO itens (id, restaurante_id, nome, categoria, status, preco, opcoes_grupos, atributos_variaveis, hash, versao)
    SELECT v.id, v.restaurante_id, v.nome, v.categoria, v.status, v.preco, v.opcoes_grupos, v.atributos_variaveis,
           v.hash, v.versao
    FROM v
    WHERE NOT EXISTS (SELECT 1 FROM itens i WHERE i.id = v.id)
"""
TEMPLATE_C2_PG = "(%s::uuid, %s::uuid, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::int, %s::bigint)"
COLUNAS_C2_PG = ["id", "restaurante_id", "nome", "categoria", "status", "preco",
                 "opcoes_grupos", "atributos", "hash", "versao"]

def upsert_lote_c2_pg(eventos):
    linhas = [tuple(e[c] for c in COLUNAS_C2_PG) for e in eventos]
    conn = engine_pg.raw_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, SQL_UPSERT_LOTE_C2_PG, linhas,
                           template=TEMPLATE_C2_PG, page_size=len(linhas))  # uma única instrução
        conn.commit()
    finally:
        conn.close()

def upsert_lote_c2_mongo(eventos):
    existentes = {d["_id"]: d for d in db_mongo.itens.find(
        {"_id": {"$in": [e["_id"] for e in eventos]}}, {"hash": 1, "versao": 1, "criado_em": 1})}
    ops = []
    for evento in eventos:
        existente = existentes.get(evento["_id"])
        if existente is None:
            ops.append(InsertOne(evento))
        elif existente["hash"] != evento["hash"] and evento["versao"] >= existente["versao"]:
            evento["criado_em"] = existente["criado_em"]
            ops.append(ReplaceOne({"_id": evento["_id"]}, evento))
    if ops:
        db_mongo.itens.bulk_write(ops, ordered=False)

def criar_fabrica_c2_pg(modo):
    # modo "insercao": os itens são apagados no `antes`, então toda repetição cai no INSERT
    # modo "atualizacao": os itens já existem (o aquecimento descartado os insere), então
    #                     as repetições medidas caem no caminho do UPDATE
    def fabrica(n, ids):
        ids_novos = [str(uuid.uuid4()) for _ in range(n)]
        estado = {"versao": 0, "eventos": None}

        def apagar():
            with engine_pg.begin() as conn:
                conn.execute(text("DELETE FROM itens WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids_novos})

        def antes():
            if modo == "insercao":
                apagar()
            # eventos gerados fora do tempo, como se já tivessem chegado prontos da fila
            estado["versao"] += 1
            estado["eventos"] = [gerar_evento_c2_pg(i, estado["versao"]) for i in ids_novos]

        def acao():
            upsert_lote_c2_pg(estado["eventos"])

        return antes, acao, apagar
    return fabrica

def criar_fabrica_c2_mongo(modo):
    def fabrica(n, ids):
        ids_novos = [ObjectId() for _ in range(n)]
        estado = {"versao": 0, "eventos": None}

        def apagar():
            db_mongo.itens.delete_many({"_id": {"$in": ids_novos}})

        def antes():
            if modo == "insercao":
                apagar()
            estado["versao"] += 1
            estado["eventos"] = [gerar_evento_c2_mongo(i, estado["versao"]) for i in ids_novos]

        def acao():
            upsert_lote_c2_mongo(estado["eventos"])

        return antes, acao, apagar
    return fabrica

# C8 e C3 — o estado original é restaurado no `antes` (e ao final, no `depois`), nos dois bancos.
# No PostgreSQL a ação faz COMMIT, igual ao C2 e ao MongoDB (que não tem rollback)
def fabrica_pg_com_restauracao(acao_pg, colunas, jsonb=()):
    def fabrica(n, ids):
        snapshot = tirar_snapshot_pg(ids, colunas)
        restaurar = lambda: restaurar_snapshot_pg(snapshot, colunas, jsonb)
        return restaurar, lambda: acao_pg(ids), restaurar
    return fabrica

def fabrica_mongo_com_restauracao(acao_mongo, campos):
    def fabrica(n, ids):
        snapshot = tirar_snapshot_mongo(ids, campos)
        restaurar = lambda: restaurar_snapshot_mongo(snapshot, campos)
        return restaurar, lambda: acao_mongo(ids), restaurar
    return fabrica

fabrica_c8_pg = fabrica_pg_com_restauracao(acao_c8_pg, ["atributos_variaveis", "modificado_em"], jsonb=("atributos_variaveis",))
fabrica_c3_pg = fabrica_pg_com_restauracao(acao_c3_pg, ["status", "hash", "modificado_em"])
fabrica_c8_mongo = fabrica_mongo_com_restauracao(acao_c8_mongo, ["tags", "modificado_em"])
fabrica_c3_mongo = fabrica_mongo_com_restauracao(acao_c3_mongo, ["status", "hash", "modificado_em"])

FABRICAS_LOTE = {
    "C2 - Inserção": {"PostgreSQL": criar_fabrica_c2_pg("insercao"), "MongoDB": criar_fabrica_c2_mongo("insercao")},
    "C2 - Atualização": {"PostgreSQL": criar_fabrica_c2_pg("atualizacao"), "MongoDB": criar_fabrica_c2_mongo("atualizacao")},
    "C8": {"PostgreSQL": fabrica_c8_pg, "MongoDB": fabrica_c8_mongo},
    "C3": {"PostgreSQL": fabrica_c3_pg, "MongoDB": fabrica_c3_mongo},
}

def rodar_lote(preset, saida, repeats, consultas=None):
    caminho = saida / "resultados_lote.json"
    marcar(saida, f"lote {preset}: carga")
    preparar_ambiente(*PRESETS[preset])

    amostra_ids = amostrar_ids(max(LOTES))

    # {consulta: {cenario: {tamanho_do_lote: resultado_medir_tempo}}}; com `consultas` (ex.: C2), parte do arquivo existente
    resultados_lote = carregar_json_existente(caminho) if consultas else {}

    for nome_consulta, fabricas in FABRICAS_LOTE.items():
        if consultas and nome_consulta.split(" - ")[0] not in consultas:  # "C2 - Inserção" entra com "C2"
            continue
        resultados_lote[nome_consulta] = {}
        marcar(saida, f"lote: {nome_consulta}")
        for cenario in CENARIOS_ESCALABILIDADE[nome_consulta.split(" - ")[0]]:  # "C2 - Inserção" usa os cenários do C2
            sgbd = "PostgreSQL" if cenario["label"].startswith("PostgreSQL") else "MongoDB"
            cenario["preparar"]()
            info = descrever_cenario(nome_consulta, cenario["label"])
            print(f"{nome_consulta} | {cenario['label']} | índice: {info['indice']} | "
                  f"espaço do índice: {info['espaco_indice_mb']:.2f} MB")
            resultados_lote[nome_consulta][cenario["label"]] = {}
            for n in LOTES:
                antes, acao, depois = fabricas[sgbd](n, amostra_ids[sgbd][:n])
                try:
                    resultado = medir_tempo(acao, repeats=repeats, warm_up=False, antes=encadear(antes, limpar_cache))
                finally:
                    if depois:
                        depois()  # devolve o banco ao estado original
                if resultado is None:
                    continue
                resultado["itens_por_lote"] = n
                resultado["ms_por_item"] = resultado["media"] / n
                resultado.update(info)
                resultados_lote[nome_consulta][cenario["label"]][str(n)] = resultado
                imprimir_resultado(f"{nome_consulta} | {cenario['label']} | lote {n}", resultado,
                                   extra=f" ({resultado['ms_por_item']:.4f} ms/item)")

    salvar_json(resultados_lote, caminho)
    print(f"Resultados salvos em {caminho}.")


# ----------------------------------------------------------------------------
# Execução
# ----------------------------------------------------------------------------
def main():
    global usar_sudo
    parser = argparse.ArgumentParser(description="Benchmark PostgreSQL (JSONB) vs MongoDB: Seção 7 do notebook.")
    parser.add_argument("experimentos", nargs="*",
                        help="volume, seletividade, lote e/ou carga (padrão: volume, seletividade e lote; a carga "
                             "também é medida dentro do volume, mas `carga` roda só ela, em poucos minutos)")
    parser.add_argument("--presets", default=",".join(PRESETS),
                        help="volumes do experimento 'volume', separados por vírgula (padrão: todos)")
    parser.add_argument("--preset", default="Grande", choices=list(PRESETS),
                        help="volume fixo de 'seletividade' e 'lote' (padrão: Grande)")
    parser.add_argument("--repeats", type=int, default=5,
                        help="repetições de cada medição, em leituras e escritas (padrão: 5)")
    parser.add_argument("--repeats-carga", type=int, default=5,
                        help="quantas vezes repetir a carga inicial de cada volume, em 'volume' e 'carga' "
                             "(padrão: 5; cada rodada recria o banco do zero)")
    parser.add_argument("--saida", default="resultados", help="pasta dos JSON (padrão: ./resultados)")
    parser.add_argument("--consultas", default=None,
                        help="refaz só estas consultas em 'volume', 'seletividade' e 'lote' (ex.: C2,C3,C8) e mantém o resto "
                             "do JSON existente (padrão: todas)")
    parser.add_argument("--sem-so", action="store_true",
                        help="não limpa o cache do sistema operacional (dispensa sudo)")
    args = parser.parse_args()

    saida = (RAIZ / args.saida) if not Path(args.saida).is_absolute() else Path(args.saida)
    presets = [x.strip() for x in args.presets.split(",") if x.strip()]
    for x in presets:
        if x not in PRESETS:
            parser.error(f"preset inválido: {x} (opções: {', '.join(PRESETS)})")

    experimentos = args.experimentos or ["volume", "seletividade", "lote"]
    for x in experimentos:
        if x not in ("volume", "seletividade", "lote", "carga"):
            parser.error(f"experimento inválido: {x} (opções: volume, seletividade, lote, carga)")

    consultas = {x.strip() for x in args.consultas.split(",") if x.strip()} if args.consultas else None
    usar_sudo = not args.sem_so
    if {"volume", "seletividade", "lote"} & set(experimentos):  # todas as medições limpam o cache
        if usar_sudo:
            checar_sudo()
        else:
            print("AVISO: o cache do sistema operacional NÃO será limpo; só o cache dos bancos.")

    reiniciar_fases(saida)
    inicio = time.perf_counter()
    if "carga" in experimentos:
        rodar_carga(presets, saida, args.repeats_carga)
    if "volume" in experimentos:
        rodar_volume(presets, saida, args.repeats, consultas, args.repeats_carga)
    if "seletividade" in experimentos:
        rodar_seletividade(args.preset, saida, args.repeats, consultas)
    if "lote" in experimentos:
        rodar_lote(args.preset, saida, args.repeats, consultas)
    print(f"\nConcluído em {(time.perf_counter() - inicio) / 60:.1f} min.")


if __name__ == "__main__":
    main()
