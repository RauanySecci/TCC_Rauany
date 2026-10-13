# O que incluir ou alterar no texto do TCC

Lista de pendências do texto, decorrentes dos ajustes no notebook `notebooks/analises.ipynb`.
Marque `[x]` quando o item entrar no texto.

## 1. Tabela de índices (incluir)

- [ ] Inserir uma tabela com consulta, banco, tipo de índice, DDL exato e tamanho em MB
  (os tamanhos vêm de `medir_espaco_indice_pg` / `medir_espaco_indice_mongo` após reexecutar).

| Consulta | Banco | Tipo | Definição |
|---|---|---|---|
| C1 | PostgreSQL | B-Tree composto | `CREATE INDEX btree_c1 ON itens (categoria, status)` |
| C1 | MongoDB | Compound | `create_index([("categoria", 1), ("status", 1)], name="btree_c1")` |
| C4 | PostgreSQL | B-Tree composto | `CREATE INDEX btree_c4 ON itens (restaurante_id, status)` |
| C4 | MongoDB | Compound | `create_index([("restauranteId", 1), ("status", 1)], name="btree_c4")` |
| C5 | PostgreSQL | B-Tree de expressão | `CREATE INDEX btree_c5 ON itens (((preco->>'valor')::numeric))` |
| C5 | MongoDB | Single-field | `create_index([("preco.valor", 1)], name="single_field_c5")` |
| C6 | PostgreSQL | GIN | `CREATE INDEX gin_c6 ON itens USING GIN (atributos_variaveis)` |
| C6 | MongoDB | Wildcard | `create_index({"$**": 1}, name="wildcard_c6")` |
| C7 | PostgreSQL | GIN | `CREATE INDEX gin_c7 ON itens USING GIN (opcoes_grupos)` |
| C7 | MongoDB | Multikey | `create_index([("opcoes_grupos.opcoes.nome", 1)], name="multikey_c7")` |

C2 e C8 (escritas) foram medidas sem índice secundário.

## 2. Subseção "Ameaças à validade" (incluir)

- [ ] **Local do dado (C6 e C8).** No PostgreSQL, `promocao` e `tags` ficam dentro do JSONB
  `atributos_variaveis`; no MongoDB, são campos de topo do documento. Os dados são os mesmos
  (gerados juntos em um único dicionário `atributos`, gravado inteiro como JSONB no PostgreSQL
  e espalhado no documento com `doc.update(...)` no MongoDB). A diferença de posição é
  consequência do modelo, que é o objeto de estudo, e não viés do método.
- [ ] **Índices de natureza diferente no C6.** O GIN cobre apenas a coluna `atributos_variaveis`;
  o wildcard `$**` indexa todos os campos do documento. A comparação de tamanho em disco deve
  ser lida com essa ressalva.
- [ ] **Custo da atualização no C8.** `jsonb_set` regrava o JSONB inteiro (nova versão da linha,
  possível TOAST); `$addToSet` altera um array de topo. É o custo real de cada modelo, não erro.
- [ ] **Valores nulos e campos ausentes.** `status <> 'DELETED'` (PostgreSQL) e `$ne` (MongoDB)
  tratam `NULL` e campo ausente de forma diferente.
- [ ] **Escopo do C6.** `tags` e `promocao` são gerados sempre juntos, então o C6 filtra o mesmo
  conjunto de itens que têm tags. Isso explica a seletividade da consulta.

## 3. Durabilidade das escritas (incluir)

- [ ] Declarar que os dois bancos foram configurados com confirmação durável de escrita:
  PostgreSQL com `synchronous_commit=on` (padrão, o `COMMIT` espera o flush do WAL) e MongoDB
  com `w=1, journal=True` (`MongoClient(mongo_uri, w=1, journal=True)`). Vale para C2, C8 e os
  lotes. Sem isso, a diferença de tempo refletiria garantia de durabilidade, e não modelagem.

## 4. Projeção do C6 (incluir na descrição da consulta)

- [ ] Os dois bancos devolvem só `nome`, `categoria` e `promocao`. No PostgreSQL a consulta usa
  `atributos_variaveis->'promocao' AS promocao`, para o volume transferido ser equivalente.

## 5. Medição de espaço em disco no MongoDB (incluir)

- [ ] Declarar que o tamanho em disco no MongoDB (dados e índices) foi medido após `fsync`
  (`client_mongo.admin.command("fsync")` antes do `collStats`), para refletir o estado após o
  checkpoint do WiredTiger. Sem isso o `storageSize` ficava defasado logo após a inserção
  (apareceu 0,00 MB; com `fsync`, 0,55 MB para 5000 itens).
- [ ] Citar que o WiredTiger comprime os dados em disco (snappy por padrão), fator que reduz o
  espaço ocupado pelo MongoDB na comparação com o PostgreSQL (`pg_table_size`: heap + TOAST).

## 6. Medição da inserção de volume (incluir)

- [ ] Descrever o método da inserção em volume: a conversão dos dados para o formato de cada banco
  (`preparar_dados_postgres` / `preparar_dados_mongo`, incluindo os `json.dumps` do PostgreSQL) é
  feita **antes** do cronômetro e do monitor; só o envio ao banco é medido. Antes, a montagem de 1
  milhão de dicionários em Python entrava no tempo e fazia o CPU do MongoDB parecer ~1%.
- [ ] Declarar que os dois bancos usam o mesmo tamanho de lote (`TAMANHO_LOTE = 10_000`):
  PostgreSQL com `psycopg2.extras.execute_values` (`page_size=10_000`, uma única transação) e
  MongoDB com `insert_many` em fatias de 10 mil documentos. A versão anterior do PostgreSQL usava
  `executemany` (um INSERT por linha, ~395 s) e não era comparável ao `insert_many` do MongoDB.
- [ ] Declarar que o CPU e a memória vêm de `docker stats` dos containers `catalogo-postgres-db` e
  `catalogo-mongo-db`, com amostras a cada ~2 a 3 s (a própria chamada do `docker stats --no-stream`
  demora), o que dá poucas amostras no MongoDB (7 a 9 em ~21 a 23 s). Registrar como limitação.
- [ ] Corrigir o ambiente: o MongoDB do notebook estava conectando no `mongod` instalado no
  sistema (porta 27017 do host), e não no container monitorado; por isso o CPU saía ~1%. O
  `mongod` do sistema foi desabilitado e o container passou a publicar a 27017. **Todas as
  medições do MongoDB anteriores a essa correção (volume e demais consultas) usaram o `mongod` do
  sistema e precisam ser refeitas no container.**
- [ ] Resultados da inserção de 1.000 restaurantes e 1.000.000 de itens (3 rodadas, média ± desvio
  padrão amostral, recriando tabelas e coleções entre as rodadas):

| | PostgreSQL | MongoDB |
|---|---|---|
| Tempo | 73,6 ± 4,8 s (77,9 / 68,4 / 74,6) | 20,9 ± 2,5 s (20,9 / 23,4 / 18,4) |
| CPU média | 75,4 ± 5,0% | 33,6 ± 4,9% |
| CPU máxima | 92,1 ± 0,6% | 47,4 ± 2,8% |
| Memória média | 286,5 ± 12,8 MB | 268,6 ± 11,7 MB |
| Espaço em disco | 647,19 MB | 107,6 MB (comprimido) |
| Tamanho lógico | 647,19 MB | 613,57 MB |

  O MongoDB foi ~3,5x mais rápido na carga. A média de CPU do MongoDB tem desvio alto porque são
  poucas amostras (7 a 9) e a última pega o fim da carga; a CPU máxima é a métrica mais estável.

- [ ] Reportar o espaço do MongoDB em duas métricas: `storageSize` (em disco, comprimido com
  snappy) e `size` (lógico, sem compressão, `medir_tamanho_logico_mongo`). Em tamanho lógico os
  bancos são equivalentes (647 contra 614 MB); a vantagem de ~6x em disco é da compressão, não
  do modelo.
- [x] Repetir a inserção 3 a 5 vezes (recriando tabelas e coleções) e reportar média e desvio (feito: 3 rodadas).

## 7. Remoções e correções (alterar)

- [ ] Remover qualquer menção à consulta CX (filtro em atributo variável, campo `gelada`), que
  saiu do notebook.
- [ ] Revisar frases que afirmem equivalência total entre C6 e C8; trocar por equivalência de
  resultado e dados, com as diferenças de modelo declaradas na seção 2.
- [ ] Atualizar todos os números de C6, C8, C2, lote e volume depois de reexecutar o notebook
  (as medições anteriores foram feitas com projeção diferente no C6 e sem `journal=True`).
