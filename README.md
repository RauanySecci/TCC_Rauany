# Análise Comparativa de Desempenho: PostgreSQL com JSONB e MongoDB em Sistemas de Catálogo com Consultas Dinâmicas

Código e ambiente experimental do Trabalho de Conclusão de Curso (ICMC/USP) que compara o desempenho do PostgreSQL, com a ferramenta JSONB, e do MongoDB em um sistema de catálogo de produtos com atributos variáveis.

## Sobre o projeto

Catálogos de produtos têm dados heterogêneos: uma pizza tem opções de borda e tamanhos, uma bebida tem volume e uma sobremesa tem informação nutricional. Guardar tudo em colunas fixas deixa a maioria delas vazia, então os atributos variáveis costumam ficar em uma coluna JSONB (PostgreSQL) ou em campos do próprio documento (MongoDB).

Este repositório implementa um recorte controlado desse cenário, inspirado em um sistema real de catálogo, e mede o tempo de execução de consultas de leitura e de escrita nos dois bancos, com diferentes estratégias de indexação e em diferentes escalas.

A pergunta que guia o trabalho: o MongoDB seria uma alternativa mais adequada para esse padrão de dados, ou o PostgreSQL com JSONB oferece desempenho comparável, mantendo a consistência relacional?

Escopo do experimento:

- Comparação direta entre os dois SGBDs, sem camada de API, em um único nó.
- Dados sintéticos, gerados de forma determinística.
- Os dois bancos executam em contêineres Docker com os mesmos limites de recursos.

## Estrutura do repositório

```
.
├── notebooks/
│   └── analises.ipynb      # implementação completa do experimento
├── docker-compose.yml      # PostgreSQL 14.18 e MongoDB 8.0.29
├── requirements.txt        # dependências Python, com versões fixadas
├── .env                    # você deve criar
└── .gitignore
```

## Pré-requisitos

- Docker (Engine ou Desktop) com Docker Compose v2, ou seja, o comando `docker compose`.
- O cliente `docker` disponível no terminal da máquina que executa o notebook, porque a medição de CPU e memória durante a inserção chama `docker stats`.
- Python 3.10 ou superior.
- VS Code com a extensão Jupyter, ou JupyterLab.
- Recursos livres para o Docker: os dois contêineres são limitados a 2 CPUs e 1 GB de memória cada. 

## Configuração

### 1. Clonar o repositório

```bash
git clone https://github.com/RauanySecci/TCC_Rauany.git
cd TCC_Rauany
```

### 2. Criar o arquivo de variáveis de ambiente

O arquivo `.env` não é versionado (está no `.gitignore`). Ele deve ficar na raiz do repositório e ter a seguinte estrutura:

```
# Credenciais do PostgreSQL (lidas pelo docker-compose.yml)
POSTGRES_USER=catalogo
POSTGRES_PASSWORD=troque-esta-senha
POSTGRES_DB=catalogo

# Conexao com o PostgreSQL usada pelo notebook (repita os valores acima)
PGUSER=catalogo
PGPASSWORD=troque-esta-senha
PGDATABASE=catalogo
PGHOST=localhost
PGPORT=5432

# Conexao com o MongoDB usada pelo notebook
MONGO_URI=mongodb://localhost:27017
MONGO_DB=catalogo
```

### 3. Subir os bancos de dados

```bash
docker compose up -d
docker compose ps
```

Serão criados dois contêineres:

| Contêiner | Imagem | Porta | Limites |
|---|---|---|---|
| `catalogo-postgres-db` | `postgres:14.18` | 5432 | 2 CPUs, 1 GB |
| `catalogo-mongo-db` | `mongo:8.0.29` | 27017 | 2 CPUs, 1 GB |

Os dados ficam em volumes Docker persistentes (`postgres_data` e `mongo_data`). 

### 4. Criar o ambiente Python

```bash
python -m venv venv
source venv/bin/activate        # Linux e macOS
venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

O `requirements.txt` lista todas as dependências instaladas no ambiente de desenvolvimento, com versões fixadas, inclusive as indiretas do Jupyter. As principais:

| Biblioteca | Versão | Uso |
|---|---|---|
| SQLAlchemy | 2.0.53 | Conexão e consultas ao PostgreSQL |
| psycopg2-binary | 2.9.13 | Driver do PostgreSQL |
| PyMongo | 4.18.1 | Conexão e consultas ao MongoDB |
| pandas | 2.3.3 | Manipulação dos resultados |
| Faker | 40.39.0 | Geração de dados sintéticos |
| python-dotenv | 1.2.3 | Leitura do `.env` |
| matplotlib / seaborn | 3.10.9 / 0.13.2 | Gráficos |
| ipywidgets | 8.1.9 | Seleção do volume de dados |
| JupyterLab | 4.6.3 | Execução do notebook |

### 5. Executar o notebook

Abra `notebooks/analises.ipynb`, execute as células na ordem, de cima para baixo. 

## O que o notebook faz

O notebook é organizado nas seguintes seções:

1. **Configuração do ambiente:** carrega as variáveis do `.env`, cria as conexões com os dois bancos e fixa as sementes (`random.seed(42)` e `Faker.seed(42)`).
2. **Estrutura do banco:** recria as tabelas `restaurantes` e `itens` no PostgreSQL e limpa as coleções do MongoDB.
3. **Geração de dados:** gera restaurantes e itens sintéticos, com atributos variáveis que mudam conforme a categoria do item (pizza, bebida, sobremesa e lanche).
4. **Funções de benchmark:** medição de tempo, de recursos durante a inserção e de espaço em disco.
5. **Inserção dos dados:** carga nos dois bancos, com medição de tempo, CPU e memória.
6. **Consultas:** implementação das consultas em SQL e em MongoDB, com e sem índices.
7. **Escalabilidade:** repetição das consultas variando a seletividade, o volume total do banco e o tamanho do lote de escrita.

### Consultas

As consultas são agrupadas em dados estruturados (C1 a C4) e dados semiestruturados (C5 a C8).

| Consulta | O que mede | PostgreSQL | MongoDB |
|---|---|---|---|
| C1 | Itens por categoria e status | Sem índice; B-tree composto | Sem índice; índice composto |
| C2 | Upsert condicional | Chave primária | Chave primária (`_id`) |
| C3 | Exclusão lógica | Chave primária | Chave primária (`_id`) |
| C4 | Itens de um restaurante (junção) | Sem índice; B-tree composto | Sem índice; índice composto |
| C5 | Itens por faixa de preço | Sem índice; B-tree de expressão | Sem índice; índice simples |
| C6 | Existência de um atributo variável | Sem índice; GIN | Sem índice; wildcard |
| C7 | Filtro em array de objetos aninhados | Sem índice; GIN | Sem índice; multikey |
| C8 | Adição de tag (atualização parcial) | Sem índice; B-tree e GIN | Sem índice; multikey |

## Limitações

- Os dados são sintéticos e o ambiente é local, com um único nó, sem concorrência de clientes.
- O experimento é um recorte do problema.
