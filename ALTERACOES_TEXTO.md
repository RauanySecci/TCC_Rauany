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
- [ ] **(Teste preliminar, substituído pela seção 17.8: usar os números das 5 rodadas finais.)** Resultados da inserção
  de 1.000 restaurantes e 1.000.000 de itens (3 rodadas, média ± desvio
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

---

# Ajustes de método feitos depois (script `benchmark.py` e notebook)

As seções abaixo vieram da revisão do método de medição da Seção 7 (Escalabilidade). Os números
citados são das rodadas **preliminares** e servem só como tendência: todos os valores do texto
devem vir da rodada final.

## 8. Limpeza de cache nas leituras (incluir no método)

- [ ] Descrever que as consultas de **leitura** (C1, C4, C5, C6 e C7) são medidas com o **cache limpo
  antes de cada repetição**: os containers são parados, o cache de páginas do sistema operacional é
  descartado (`sync; echo 3 > /proc/sys/vm/drop_caches`, exige `sudo`) e os containers sobem de novo;
  só depois a consulta é cronometrada (a limpeza fica fora do cronômetro). Foi uma sugestão da
  orientadora. Vale para os experimentos de **volume** e de **seletividade**.
- [ ] Declarar que **não há execução de aquecimento** nas leituras (ela esquentaria o cache).
- [ ] Dizer que isso representa o **pior caso** (primeira leitura depois de reiniciar o banco). Em
  produção os dados mais acessados tendem a ficar em cache, então os tempos reais costumam ficar
  abaixo dos medidos. Não chamar de "comportamento típico do dia a dia".
- [ ] Registrar o efeito de reiniciar o banco a cada repetição: inclui custos de "primeira consulta"
  (catálogos internos, abertura de arquivos). No volume Pequeno isso é um custo fixo de ~30 a 55 ms
  por consulta, então a comparação no Pequeno reflete sobretudo esse custo.
- [ ] Se o `sudo` não estiver disponível e for usado `--sem-so`, declarar que só o cache dos bancos
  foi limpo e o do sistema operacional continuou quente.
- [ ] **Escritas (C2, C3, C8) e lote não limpam o cache.** Justificar: o custo dominante é o
  commit no log de transações (WAL no PostgreSQL, journal no MongoDB), e cada escrita toca poucas
  páginas. Ressalvas a registrar: (a) uma escrita por chave lê a página do item antes de modificá-la,
  então com cache frio ela seria um pouco mais lenta; (b) reiniciar o PostgreSQL a cada repetição
  faria a primeira escrita em cada página gravar a página inteira no log (`full_page_writes`), o que o
  penalizaria artificialmente frente ao MongoDB; (c) o tempo cresceria muito. **Pendente:** perguntar
  à orientadora se ela espera a limpeza também nas escritas.
- [ ] Declarar o número de repetições: **10 em todas as medições** (leituras e escritas; decidido na seção 17;
  a carga inicial é repetida 5 vezes). Não há execução de aquecimento descartada. Os gráficos usam a **média**
  (com mínimo, máximo e quartis nos gráficos 1 e 4); a mediana ficou como apoio na discussão, por ser menos
  sensível a picos isolados. Detalhes na seção 17.7.
- [ ] Descrever o monitoramento (opcional): `monitorar.py` registra CPU e memória dos containers e
  do PC a cada 2 s; para os resultados finais, rodar **sem** o monitor (ele consome CPU).

## 9. O que o tempo medido inclui (incluir)

- [ ] Declarar que o tempo é **de ponta a ponta**, medido com cronômetro externo
  (`time.perf_counter`): execução no banco + transferência dos dados + conversão do resultado para
  DataFrame. O tempo interno do SGBD (`EXPLAIN ANALYZE` / `executionStats`) **não** é usado nas
  medições. (A célula 4.1 do notebook foi corrigida: dizia o contrário.)
- [ ] Registrar como limitação que a conversão usa ferramentas diferentes em cada banco
  (`pd.read_sql` no PostgreSQL e `pd.DataFrame(list(cursor))` no MongoDB, cada uma com seu driver).
  Nas consultas que devolvem muitas linhas (C1, C5, C6 e C7) essa parte pesa muito: em um teste do C5
  no MongoDB, o servidor levou ~1,2 s e a medição de ponta a ponta ~2,9 s.
- [ ] O `EXPLAIN` é usado apenas para **descrever o cenário** (índice usado e tipo de acesso), fora
  da cronometragem.
- [ ] Lembrar que o C4 no MongoDB faz duas consultas (busca o restaurante e depois os itens), e no
  PostgreSQL é uma junção em uma consulta só.

## 10. Configuração dos containers (incluir e corrigir)

- [ ] Os dois containers foram configurados com **2 CPUs e 3 GiB de memória** (antes era 1 GiB).
  **Corrigir o `README.md`**: as linhas 37, 81 e 82 ainda dizem 1 GB.
- [ ] Declarar a configuração de cache de cada banco: o MongoDB ajusta o cache do WiredTiger
  sozinho (50% de (limite - 1 GB)), ficando em **1 GiB** com 3 GiB; o PostgreSQL ficou com o
  `shared_buffers` **padrão (128 MB)**, sem ajuste. Registrar como limitação que o PostgreSQL não foi
  ajustado de forma equivalente (um teste com `shared_buffers` maior, ~1 GB, é opcional).
- [ ] **Análise de sensibilidade à memória (opcional, mas rendeu um achado).** Com 1 GiB por
  container (cache do MongoDB de 256 MiB, menor que os ~700 MB de dados), no volume Grande e com
  cache quente: C5 com índice no MongoDB 22.716 ms contra 2.916 ms sem índice; C6 com wildcard
  7.827 ms contra 1.912 ms; C1 com índice 1.647 ms contra 1.427 ms. Com 3 GiB (cache de 1 GiB) o
  problema desapareceu: C5 2.769 ms, C6 1.680 ms, C1 1.082 ms (índice equivalente à varredura). Medido
  no C5: com o índice o MongoDB leu ~38 GB para o cache (para buscar ~497 mil documentos um a um) contra
  ~0,5 GB na varredura. Conclusão a escrever: um índice mal aproveitado pode degradar a consulta em
  até 8 vezes quando o cache é pequeno. Os números do 1 GiB precisam ser refeitos se forem citados.

## 11. Índice criado, mas ignorado pelo planejador (incluir)

- [ ] Registrar, para cada cenário, o **índice efetivamente usado**, o **tipo de acesso** (`Seq Scan`,
  `Bitmap Heap Scan`, `COLLSCAN`, `IXSCAN`) e o **espaço do índice**. O benchmark grava isso nos
  JSON (`indice`, `tipo_acesso`, `espaco_indice_mb`). Acrescentar essas colunas à tabela da seção 1.
- [ ] No PostgreSQL, no volume Grande, o planejador **ignorou** o B-tree no C1 e no C5
  (`Seq Scan`, índice `nenhum`), porque as consultas devolvem uma fração enorme da tabela (~35% e
  ~47%). O índice ocupa espaço (7 e 21 MB) e não é usado. Explicar com a seletividade.
- [ ] Quando o PostgreSQL ignora o índice, o benchmark mede também a variante **"(índice
  forçado)"** (`SET enable_seqscan = off`), só nesses casos, para mostrar o custo real do índice. Citar
  no texto que o planejador não foi forçado nos demais cenários. O MongoDB não precisou de `hint`: o
  plano vencedor foi `IXSCAN` em todos os cenários com índice.
- [ ] O C6 do PostgreSQL (GIN) **mudou de plano entre rodadas** (`Bitmap Heap Scan` em uma,
  `Seq Scan` em outra): está na fronteira entre usar ou não o índice. Reportar o plano de cada rodada.

## 12. Preparação do banco depois da carga (incluir)

- [ ] Declarar que, depois de carregar os dados, o benchmark roda `ANALYZE` nas tabelas do
  PostgreSQL, para todos os cenários partirem das mesmas estatísticas do planejador (antes, só os
  cenários com índice rodavam `ANALYZE`, e isso mudava o plano entre "sem índice" e "com índice"). O
  `ANALYZE` feito ao criar cada índice continua: é necessário para o índice de expressão do C5.
- [ ] Explicar a diferença entre os bancos: o PostgreSQL decide o plano com estatísticas guardadas
  (por isso o `ANALYZE`); o MongoDB, por padrão, testa os planos candidatos na hora e guarda o
  vencedor em um cache de planos. O MongoDB não tem etapa equivalente.
- [ ] Registrar como limitação que o cache de planos do MongoDB é perdido a cada reinício, então com
  o cache limpo ele refaz o teste de planos em cada repetição (custo não medido, provavelmente
  desprezível exceto em consultas de poucos milissegundos, como o C4 com índice).
- [ ] No experimento de **lote**, depois da carga há um `CHECKPOINT` (PostgreSQL), um `fsync`
  (MongoDB) e uma pausa de 10 s, para a gravação em segundo plano não contaminar as medições
  (no teste com 1 milhão de itens o disco ficou saturado ~35 s depois da carga). Volume e
  seletividade não precisam disso, porque o reinício antes de cada repetição já grava tudo.

## 13. Numeração e ordem das consultas (conferir)

- [ ] A numeração atual é: C1 itens por categoria e status; C2 upsert; C3 soft delete; C4 junção;
  C5 faixa de preço; C6 existência de atributo; C7 array aninhado; C8 atualização parcial (igual à
  do README). Conferir se o texto do TCC usa essa mesma numeração (em 03/10 o upsert era C7).
- [ ] A **ordem de execução** não segue a numeração: C1, C5, C6, C4, C7, C2, C8, C3 (leituras
  primeiro, escritas por último). Se citar a ordem, dizer isso. Foi herdada da numeração antiga e
  mantida de propósito, para as escritas não alterarem as leituras seguintes.
- [ ] O arquivo `notebooks/resultados_volume.json` (de 10/10) usa a numeração **antiga**: não usá-lo
  nos gráficos.

## 14. Tendências das rodadas preliminares (para a discussão; confirmar na rodada final)

> **Números preliminares, substituídos pela rodada final de 10 repetições (seção 17). Não citar os valores desta seção;
> eles ficam só como lembrete das tendências.**

- [ ] A **junção (C4)** é a consulta em que o índice mais pesa. (Preliminar, cache quente: de ~400 a ~770 ms para ~2 ms.)
  **Rodada final, volume Grande, cache limpo:** PG 1250 → 65 ms (~19×) e Mongo 1164 → 110 ms (~11×).
- [ ] Nas consultas que devolvem boa parte da tabela (C1, C5, C6, C7), o índice **empata ou piora**;
  só o multikey do MongoDB no C7 melhora de forma clara (~30%).
- [ ] O custo do cache limpo diminui com o volume: no Pequeno é 3 a 25 vezes o tempo com cache
  quente, no Médio 1,2 a 5 vezes, no Grande 1 a 2 vezes nas varreduras. Consultas por índice sofrem
  mais (C4 com índice ficou 32 a 46 vezes mais lenta que a quente).
- [ ] O ranking entre os bancos muda conforme o cache: em algumas consultas o MongoDB ganha com cache
  quente e o PostgreSQL passa à frente com o cache limpo (C1 e C6 sem índice).
- [ ] As escritas (C2, C3 e C8) **não variam com o volume**. (Preliminar: ~1 a 3 ms; **rodada final**, com cache limpo: PG ~14–28 ms
  e Mongo ~7–19 ms.)
- [ ] A variação entre rodadas é de ~10 a 15%: não tirar conclusões de diferenças menores que isso. (Na rodada final, em
  1 milhão de itens a variação entre repetições é menor, mas em 5 mil itens passa de 40%; ver seção 17.2.)

## 15. Limitações gerais (incluir)

- [ ] Dados sintéticos (semente fixa), cliente único e sequencial (sem concorrência), uma só máquina
  de uso pessoal (registrar CPU, RAM e disco do PC), versões PostgreSQL 14.18 e MongoDB 8.0.29.
- [x] Uma rodada completa com **10 repetições** por medição (feito) e carga inicial repetida **5 vezes** (feito, seção 17.8).
  Ainda vale registrar que cada banco foi recarregado do zero em cada volume e que a rodada final foi uma só.
- [ ] Guardar junto com os resultados o commit do repositório e a configuração usada.

## 16. Pendências de decisão e de arquivos

- [ ] Resposta da orientadora: (1) usar só o cache limpo nas leituras; (2) reiniciar o banco a cada
  repetição ou limpar só o cache do banco; (3) cache nas escritas; (4) número de repetições (decidido: 10, ver seção 17).
- [ ] Apagar `notebooks/analises.ipynb.bak` (cópia do notebook antes das alterações) depois de
  conferir o notebook novo.
- [ ] As consultas avulsas da Seção 6 do notebook ainda usam `repeats=10`; só mudar se os números
  delas forem citados no texto.
- [ ] A célula 4.1 do notebook ainda cita o parâmetro `nome_container` em `medir_tempo`, que não
  existe mais na função.
- [x] Rodar de novo todos os experimentos (feito: rodada de 10 repetições, `resultados_final_10_repeticoes/`). Usar só esses números no texto.

## 17. Rodada final (10 repetições) e ajustes nos gráficos (16–17/10)

Todos os números desta seção vêm de `resultados_final_10_repeticoes/` (a partir de 17.3, **média** das 10
repetições em ms, como nos gráficos; entre parênteses, a faixa mínimo–máximo quando relevante). É a rodada que vale para o texto. A pasta antiga com 5 repetições
(`resultados_final/`) foi **apagada** (17/10); os números de 5 repetições citados nas seções anteriores a esta não valem.

### 17.1 Rodada e pastas

- [ ] Comando: `python benchmark.py --repeats 10 --saida resultados_final_10_repeticoes` (180 min; volume,
  seletividade e lote, este último e a seletividade no volume Grande). Gráficos:
  `python graficos.py --entrada resultados_final_10_repeticoes` → `resultados_final_10_repeticoes/graficos/`.
- [ ] Declarar no texto: **10 repetições** por medição, cache do banco e do sistema operacional limpos antes de
  cada repetição, sem aquecimento, valor reportado = mediana.
- [ ] Limite do script: a C2 gera só 11 eventos de teste por cenário (`range(11)` em `preparar_c2_pg` e
  `preparar_c2_mongo`), então `--repeats` acima de 10 faz a C2 falhar (`StopIteration`, capturado por
  `medir_tempo`, que devolve `None`). Com 10 funciona.
- [ ] Limpeza: o arquivo `resultados_10_repeticoes.log` (raiz do projeto) pode ser apagado; as pastas
  `resultados/`, `resultados_10_repeticoes/` e `resultados_repeticao/` já foram apagadas.

### 17.2 Variação entre execuções (ameaças à validade)

Só dados da rodada final de 10 repetições (nada de testes preliminares ou com outro número de repetições).

- [ ] O tempo do MongoDB com índice nas escritas por id (C2, C3, C8) **alterna entre dois patamares** em qualquer volume:
  um mais rápido (~5–10 ms) e outro mais lento (~13–20 ms). Exemplo, C3 por id, 1 milhão, as 10 repetições em ordem
  crescente: 6,5 · 7,9 · 13,6 · 15,3 · 16,0 · 16,2 · 16,2 · 16,3 · 16,5 · 16,6 ms (média 14,1; mediana 16,1). Outro, C8 por
  id, 100 mil: 5,6 · 7,1 · 7,5 · 7,7 · 12,5 · 12,9 · 13,5 · 13,7 · 14,0 · 16,6 ms (média 11,1; mediana 12,7). Em
  cada caso (C2, C3 e C8, nos 3 volumes), de 0 a 4 das 10 repetições (em geral 2 ou 3) ficam abaixo de 11 ms.
  Causa não confirmada (hipótese: estado do cache/processo depois do restart a cada repetição).
- [ ] Consequência: com índice, o tempo do MongoDB é **estável com o volume**, em torno de 11 a 18 ms (média), e não
  melhora nem piora com o crescimento da base. Não escrever que o MongoDB "melhora com o volume".
- [ ] **Não comparar em ms absolutos** o MongoDB com o PostgreSQL *com índice* nas escritas por id: a diferença
  (poucos ms; média ~14 contra ~25 ms em 1 milhão) é do tamanho da variação do próprio MongoDB (faixa de ~5 a 20 ms).
  Escrever "mais rápido, com diferença de poucos milissegundos". A comparação com × sem índice (ordens de grandeza)
  continua firme.
- [ ] **Por que 10 repetições:** cada repetição reinicia os containers e limpa o cache do sistema operacional, então o
  número de repetições é um compromisso com o tempo da rodada (a rodada completa levou ~3 horas). Com 10 há uma
  distribuição mínima para descrever (média, mínimo, máximo e quartis, como nos gráficos 1 e 4).
- [ ] **Volume Pequeno (5 mil itens):** a diferença entre com e sem índice fica dentro da variação entre
  repetições (ex.: C1, PG sem índice 15–63 ms, com índice 22–57 ms). **Não concluir** sobre o índice nesse
  volume, e tratar a comparação entre os bancos aí com cautela (as faixas se sobrepõem).
- [ ] Mostrar mínimo e máximo junto da média nas tabelas (estão nos JSON, campos `min`, `max` e `tempos_brutos`).
- [ ] Atualizar o item da seção 14 "escritas ficam em ~1 a 3 ms": na rodada final as escritas pontuais ficam
  em ~13–28 ms (PostgreSQL ~14–28 ms; MongoDB ~7–19 ms) e continuam sem variar com o volume.

### 17.3 Análise do gráfico 1: C1 (categoria e status) — valores em **média**

Filtro `categoria = 'pizza'` e `status <> 'DELETED'`, ~332 mil de 1 milhão de itens (~33%, baixa seletividade).
Índice B-Tree composto (categoria, status). Média das 10 repetições (mínimo–máximo entre parênteses), em ms.

| Volume | PG sem índice | PG com índice | PG forçado | Mongo sem índice | Mongo com índice |
|---|---|---|---|---|---|
| 5 mil | 41,1 (15–63) | 38,5 (22–57) | n/d | 29,2 (19–40) | 30,4 (12–42) |
| 100 mil | 126,9 | 292,6 | n/d | 178,5 | 164,7 |
| 1 milhão | 1175,4 | 1168,8 (Seq Scan, índice ignorado) | **2487,7** | 1762,8 | 1693,1 |

- [ ] **Volume:** o tempo cresce com o volume nos dois bancos, com ou sem índice (5 mil → 1 milhão, sem índice: PG 41,1 →
  1175,4 ms, ~29×; Mongo 29,2 → 1762,8 ms, ~60×).
- [ ] **Índice no PostgreSQL:** em 100 mil **piora** (+131%: 126,9 → 292,6 ms, ~2,3×, Bitmap Heap Scan); em 1 milhão o
  planejador o **ignora** (Seq Scan; 1168,8 contra 1175,4 ms, diferença desprezível) e escolhe corretamente; com o índice
  **forçado** (Bitmap Heap Scan) vai a 2487,7 ms, ~2,1× mais lento que o Seq Scan (+1312 ms). Em 5 mil, sem diferença
  conclusiva.
- [ ] **Índice no MongoDB:** sempre IXSCAN; ganho pequeno: −7,7% em 100 mil (178,5 → 164,7 ms) e −4,0% em 1 milhão
  (1762,8 → 1693,1 ms). Em 5 mil as faixas se sobrepõem (sem conclusão).
- [ ] **Por que o índice não ajuda:** o resultado é grande. No gráfico 3 (seletividade, 1 milhão, média), com menos linhas
  o MongoDB ganha mais com o índice (sobremesa, 95 mil linhas: 1319 → 991 ms, −25%; bebida, 190 mil: 1522 → 1274 ms,
  −16%; pizza, 332 mil: 1759 → 1671 ms, −5%), enquanto o PostgreSQL piora (sobremesa 769 → 5398 ms, 7,0×; bebida
  923 → 3442 ms, 3,7×; pizza: índice ignorado, 1176 contra 1184 ms). A seletividade alta que ajuda o índice só existe em C4
  (junção) e nas escritas por id.
- [ ] **Melhor banco:** em 5 mil as faixas se sobrepõem (sem conclusão); em 100 mil, PG sem índice (126,9 vs 178,5 ms, ~1,4×)
  e Mongo com índice (164,7 vs 292,6 ms, ~1,8×, só porque o índice piorou o PG); em 1 milhão, PG nos dois cenários
  (sem índice 1175 vs 1763 ms, ~1,5×; com índice 1169 vs 1693 ms, ~1,4×).
- [ ] **Espaço do índice** (5 mil / 100 mil / 1 milhão): PG 0,05 / 0,70 / 6,79 MB; Mongo 0,04 / 0,52 / 5,05 MB
  (~25% menor no Mongo).

### 17.4 Análise do gráfico 1: C2, C3 e C8 por id (escritas por chave) — valores em **média**

Filtro por id (1 linha, seletividade máxima). "Sem índice" = varredura forçada (o índice do id não pode ser
removido). **O índice sempre ajuda e nunca atrapalha; não houve índice forçado** (o planejador sempre usou o
índice). Com índice o tempo é constante com o volume; sem índice cresce. Média das 10 repetições, em ms.

| | PG com | PG sem | Mongo com | Mongo sem |
|---|---|---|---|---|
| C2, 5 mil | 22,7 | 32,9 | 16,5 | 24,7 |
| C2, 100 mil | 20,4 | 107,7 | 13,7 | 149,1 |
| C2, 1 milhão | 24,8 | 1200,5 | 14,2 | 1557,5 |
| C3, 5 mil | 26,6 | 37,7 | 18,5 | 23,9 |
| C3, 100 mil | 24,0 | 93,8 | 14,6 | 126,6 |
| C3, 1 milhão | 24,7 | 719,6 | 14,1 | 1194,8 |
| C8, 5 mil | 24,3 | 33,3 | 14,9 | 25,6 |
| C8, 100 mil | 24,0 | 89,0 | 11,1 | 122,4 |
| C8, 1 milhão | 25,2 | 721,6 | 11,3 | 1142,6 |

- [ ] **Ganho do índice (média sem ÷ média com), 100 mil / 1 milhão:** C2: PG 5,3× / 48×, Mongo 10,9× / 109×. C3: PG
  3,9× / 29×, Mongo 8,7× / 85×. C8: PG 3,7× / 29×, Mongo 11× / 101×. Em 5 mil, dentro da variação (inconclusivo; ~1,3× a
  1,7×).
- [ ] **Com índice:** MongoDB mais rápido nos três volumes (1 milhão: C2 14,2 vs 24,8 ms; C3 14,1 vs 24,7 ms; C8 11,3 vs
  25,2 ms, ~1,7× a 2,2×), mas ver 17.2 (variação do Mongo, faixa de ~5 a 20 ms): afirmar só "mais rápido, com
  diferença de poucos milissegundos".
- [ ] **Sem índice:** PostgreSQL melhor a partir de 100 mil (C2: 107,7 vs 149,1 ms em 100 mil e 1200,5 vs 1557,5 ms em 1
  milhão; C3: 93,8 vs 126,6 ms e 719,6 vs 1194,8 ms; C8: 89,0 vs 122,4 ms e 721,6 vs 1142,6 ms). Em 5 mil as faixas se
  sobrepõem.
- [ ] **A C2 do PostgreSQL usa upsert sem `ON CONFLICT`** (UPDATE condicionado + INSERT se não existe, `SQL_UPSERT_C2_PG`
  no benchmark.py), **nos dois cenários e em todo o trabalho**, porque o `ON CONFLICT` sempre usa a chave primária e não
  dá para desligá-la. Justificativa, custo e texto sugerido: seção 17.9. Os tempos da C2 por id no PG ficam acima dos que
  o `ON CONFLICT` daria; para citar o custo de uma escrita pontual, preferir a C3 ou a C8 por id.

### 17.5 Decisões sobre o uso dos gráficos no texto

- [x] **Gráfico 4 (escritas por volume): removido.** O `graficos.py` não o gera mais e os PNG foram apagados das
  duas pastas de resultados (a numeração dos demais gráficos foi mantida). O resultado ("escrever 1 item custa
  o mesmo em qualquer volume") entra em uma frase, apoiada nos gráficos 1 por id (C3/C8) e no 6 (tempo de carga; era o 7).
- [ ] **Gráfico 6 (tempo de carga; era o 7):** usar como contraste: escrita pontual constante × carga em massa que
  cresce com o volume. Envio dos dados, 5 mil → 1 milhão (**média de 5 rodadas**): PG 0,35 s → 59,2 s; Mongo 0,15 s →
  12,4 s (tempo total: PG 0,45 s → 74,9 s; Mongo 0,16 s → 15,9 s). O Mongo foi ~4,8× mais rápido na carga de 1 milhão
  (4,0× a 5,6× entre as rodadas). Espaço em disco: gráfico 9. Detalhes e variação na seção 17.8.
- [ ] **Texto da C1 (versão curta aprovada):** tempo cresce com o volume; índice ajuda pouco porque o resultado
  é grande; PG decide sozinho e o forçado piorou (~2×); volume pequeno não conclusivo; PG melhor a partir de
  100 mil. Falta incluir os números do PG piorando em 100 mil (+131%) e do Mongo (−8% e −4%), e **retirar a
  frase "Mongo melhor em 5 mil"** (na rodada de 10 repetições as faixas se sobrepõem).
- [ ] Textos da C2 e da C3: escrever com os números de 17.4 e a ressalva de 17.2.

### 17.6 Mudanças no `graficos.py` (valem para qualquer pasta de resultados)

- [x] **Gráfico 2** (`grafico_ganho`): as consultas aparecem em ordem C1, C2, C3, ..., C8 (antes: leituras
  primeiro, depois C2/C3/C8 por id). A tabela de planos segue a mesma ordem.
- [x] **Gráfico 3** (`grafico_seletividade`): removida a coluna/barra, a linha da tabela e a nota do **índice
  forçado** (aqui só se comparam os tempos entre seletividades). Quando o PostgreSQL criou o índice mas usou
  Seq Scan, a barra "com índice" é **pontilhada**, com item próprio na legenda ("índice ignorado (Seq Scan)")
  e nota no rodapé, em vez de aparecer como se o índice tivesse sido usado. O gráfico 1 continua mostrando o
  índice forçado.
- [x] **Gráfico de lote** (`grafico_lote`, hoje gráfico 4): os rótulos "50.000" e "100.000" do eixo x se sobrepunham; o segundo agora
  desce uma linha (vale para tiques com razão menor que 3 entre si).
- [ ] O `dados_graficos.csv` ainda guarda as linhas do índice forçado da seletividade; remover se não for citar.
- [ ] Ainda não ajustado: rótulos "2,0×" e "2,1×" (C4) colados no gráfico 2, e C6 do Mongo sem barra visível
  (ganho ~1,0×).
- [x] **Gráfico 4** (`grafico_escritas`): a chamada foi removida do `main`; a função continua no arquivo, sem uso.
  O `dados_graficos.csv` deixou de ter as linhas `4_escritas_volume`. O trecho da C2 por id no PostgreSQL (17.4)
  continua valendo para quem citar a escrita pontual.
- [x] **Gráfico 1 agora só com média** (`grafico1_escala_<C>.png`; a versão com mediana foi removida): ponto = média das 10 repetições; barra grossa = quartis (Q1 a Q3, interpolação linear do numpy);
  fio fino = mínimo a máximo. Sugestão da orientadora (média + medidas de distribuição). No `dados_graficos.csv`:
  `1_escala_volume_media` (y = média, colunas min/max = mínimo e máximo) e `1_escala_volume_Q1_Q3` (y = Q1,
  coluna min = Q3). Falta decidir se os gráficos 2, 3 e 5 também passam a usar a média (e então recalcular os
  números do texto, que hoje são medianas).
- [x] Tentativas descartadas: boxplot (não gostou do formato) e barras de ± 1 desvio padrão (substituídas pelos
  quartis e mínimo/máximo).
- [ ] Média, desvio padrão, quartis e mínimo/máximo de cada medição podem ser calculados a partir de
  `tempos_brutos` nos JSON, se a orientadora pedir uma tabela de apoio.

### 17.7 Média, mediana e como ler o gráfico 1 com média (para o texto)

- [ ] **Decisão pendente:** a orientadora sugeriu usar a **média** nos gráficos, acompanhada de uma medida de
  distribuição (mínimo, máximo, quartis). Até agora os gráficos 2, 3 e 5 e todos os números desta seção usam a
  **mediana** (mais robusta a valores isolados). Se passar para a média, recalcular os números do texto (os
  ganhos do índice, por exemplo, são razões entre medianas). Os JSON guardam `media`, `mediana`, `min`, `max` e
  `tempos_brutos`, então nada precisa ser rodado de novo.
- [ ] Argumentos para registrar no texto: a mediana é menos afetada por uma repetição muito fora do normal; a
  média usa todos os valores e é mais estável com poucas repetições (a mediana "pula" entre os dois patamares do
  MongoDB). Em 25 das 116 medições de volume a diferença entre média e mediana passa de 10%, quase todas do
  MongoDB em volumes pequenos; as conclusões principais (efeito do índice, crescimento com o volume) não
  dependem da escolha.
- [ ] **Legenda sugerida para o gráfico 1 com média:** "Ponto: média das 10 repetições. Barra grossa: intervalo
  entre o primeiro e o terceiro quartil (Q1 a Q3), isto é, a metade central das repetições. Fio fino: menor e
  maior tempo observado."
- [ ] **Quartis, em uma frase para o texto:** Q1 é o valor abaixo do qual ficam 25% das repetições e Q3 o valor
  abaixo do qual ficam 75%; entre os dois está a metade central dos tempos. Barra curta = repetições parecidas;
  barra comprida = tempos que variam muito entre execuções.
- [ ] **Como interpretar:** se as barras de duas séries não se sobrepõem, a diferença entre elas é real; se se
  sobrepõem (caso de 5 mil itens), não dá para afirmar qual é melhor.
- [ ] Exemplo para ilustrar (Mongo, C1, 5 mil itens, com índice; 10 repetições ordenadas): 11,8 · 13,7 · 14,1 ·
  21,5 · 38,8 · 39,9 · 40,0 · 40,2 · 41,8 · 41,9. Média 30,4 ms, mediana 39,4 ms, Q1 ≈ 16,0 ms, Q3 ≈ 40,1 ms,
  desvio padrão 13,3 ms. Os tempos se agrupam em dois patamares (~13 e ~40 ms), então a média quase não ocorre
  de fato; por isso a barra de variação importa.
- [ ] Contagem usada para escolher a barra: em 1160 medições individuais, ~70% dos valores caem dentro de
  média ± 1 desvio padrão e ~97% dentro de ± 2 desvios; a barra de quartis e o mínimo/máximo mostram a
  distribuição sem essa ambiguidade.
- [x] **Gráfico 2 agora usa a média** (decisão: volume Médio, 100 mil itens): `grafico2_ganho_indice.png`, ganho =
  média sem índice ÷ média com índice. As versões com mediana e as versões do volume Grande foram removidas.
  Valores (PG / Mongo): C1 0,4×/1,1×; C2 5,3×/11×; C3 3,9×/8,7×; C4 2,0×/2,2×; C5 0,9×/0,8×; C6 0,5×/1,0×;
  C7 0,7×/1,4×; C8 3,7×/11×. Para citar o caso do volume Grande (1 milhão), os ganhos com a média são: C2 48×/109×,
  C3 29×/85×, C4 20×/11×, C8 29×/101×, C1 1,0× (índice forçado 0,5×), C5 0,9×, C6 0,6×/0,9×, C7 0,7×/1,3×.
  CSV: linhas `2_ganho_indice`, coluna x = "Médio".
- [x] **Gráfico 3 (seletividade) agora só com média** (`grafico3_seletividade_<C>.png`, C1, C4, C5 e C7; sem barras de
  variação). As versões com mediana e com variação (traço Q1–Q3 e fio mínimo–máximo, descartada porque a variação,
  em geral ~13% da média, é pequena perto das diferenças mostradas) foram removidas. CSV: linhas `3_seletividade_media`.
  Os valores citados nas seções anteriores sobre seletividade (ex.: C1, sobremesa, Mongo 1350 → 970 ms) são medianas;
  com a média ficam, por exemplo: PG sem índice 769 ms, PG com índice 5398 ms, Mongo sem índice 1319 ms e com
  índice 991 ms (C1, sobremesa). Conferir no `dados_graficos.csv` antes de escrever.
- [x] **Gráfico de lote agora é o gráfico 4, só com média** (`grafico4_lote_<op>.png`: C2_insercao, C2_atualizacao, C8 e
  C3). Igual ao gráfico 1: ponto = média; barra grossa = Q1 a Q3; fio = mínimo a máximo, tudo dividido pelo número
  de itens do lote. A versão com mediana foi removida. CSV: `4_lote_media` (y = média, min/max) e `4_lote_Q1_Q3`.
  Numeração atual (sequência completa): 1 escala por volume, 2 ganho do índice, 3 seletividade, 4 lote,
  5 espaço do índice, 6 tempo de carga, 7 CPU da carga, 8 memória da carga, 9 espaço dos dados em disco.
  Antes era 1, 2, 3, 5 (lote), 6 (espaço), 7, 8, 9, 10 (as seções 1 a 16 e o gráfico 4 antigo, removido, ainda
  podem citar a numeração antiga).

### 17.8 Carga inicial repetida 5 vezes (agora parte do `benchmark.py`)

- [x] **Verificação feita:** a carga dos 3 volumes foi repetida 5 vezes (recriando os bancos a cada rodada) para ver se
  ela varia muito. Os dados estão em `resultados_final_10_repeticoes/resultados_carga_rodadas.json` ({volume: [5
  rodadas]}); a pasta temporária `resultados_carga_repetida/` e os scripts `repetir_carga.py` e
  `graficos_carga_repetida.py` foram removidos.
- [x] **`benchmark.py`:** a carga de cada volume passa a ser repetida 5 vezes por padrão (`--repeats-carga`, em
  `volume` e em `carga`): `carregar_varias_vezes` recria o banco do zero a cada rodada, e a última carga fica no banco
  para as consultas. `resultados_carga.json` guarda a última rodada e `resultados_carga_rodadas.json` guarda todas.
  Custo: 4 cargas extras por volume (a carga de 1 milhão leva ~1 a 2 min).
- [x] **`graficos.py`:** com `resultados_carga_rodadas.json`, os **gráficos 6 (tempo), 7 (CPU) e 8 (memória)** mostram a média
  das rodadas com fio de mínimo a máximo. Só o gráfico 9 (espaço em disco, que não varia) continua com a última rodada.
  CSV: `grafico6_carga_tempo`, `grafico7_carga_cpu`, `grafico8_carga_memoria`. Um gráfico de variação entre rodadas (desvio ÷
  média) foi gerado e **removido** (não era necessário); a variação está resumida nos números abaixo.
- [ ] **Tempo de envio (média ± desvio, 5 rodadas):** 5 mil: PG 0,35 ± 0,17 s, Mongo 0,15 ± 0,11 s; 100 mil: PG 5,4 ±
  0,8 s, Mongo 1,7 ± 0,4 s; 1 milhão: PG 59,2 ± 2,5 s (56,7–63,1), Mongo 12,4 ± 1,7 s (10,7–14,3).
- [ ] **Variação (desvio ÷ média) do tempo de envio:** 5 mil: 49% (PG) e 71% (Mongo); 100 mil: 14% e 21%; 1 milhão: 4% e
  14%. A variação cai com o volume (em 5 mil o tempo é de ~0,1 a 0,7 s).
- [ ] **O MongoDB foi mais rápido em todas as rodadas e volumes** (razão PG ÷ Mongo, média das rodadas): ~2,7× em 5 mil,
  ~3,4× em 100 mil e ~4,8× em 1 milhão (4,0× a 5,6×). A conclusão é firme; o valor exato da razão varia.
- [ ] **A medição única da rodada principal** (PG 49,1 s, Mongo 13,2 s em 1 milhão) ficou abaixo de todas as 5 rodadas
  do PG (56,7 a 63,1 s). Citar a média das 5 rodadas (59,2 s e 12,4 s) ou dizer que a carga varia ~5 a 15% em 1 milhão.
- [ ] **Espaço em disco** praticamente não varia (PG 647,2 MB; Mongo 107,7 a 108,4 MB; variação < 0,5%).
- [ ] **CPU e memória médias** variam bastante nos volumes pequenos (poucas amostras do monitor: 1 a 3 em 5 mil itens;
  ~26 e ~6 em 100 mil; ~284 e ~52 em 1 milhão). Em 1 milhão: CPU média PG 58,7 ± 3,4% e Mongo 37,7 ± 3,0%; pico PG
  103,9 ± 2,4% e Mongo 82,6 ± 7,2%. A memória do Mongo oscila mais (média 492 ± 69 MB, pico 742 ± 128 MB) que a do PG
  (313 ± 12 MB, pico 426 ± 4 MB).
- [ ] **Texto sugerido (limitação):** "A carga inicial foi repetida cinco vezes para verificar sua estabilidade; em 1
  milhão de itens o tempo de envio variou cerca de 4% (PostgreSQL) e 14% (MongoDB) entre as rodadas, bem menos que a
  diferença entre os bancos (~4,8×). Em volumes pequenos a variação relativa é grande (acima de 50%), pois o tempo
  total é inferior a 1 s."
- [x] Numeração final dos gráficos: **1 a 9** (1 escala, 2 ganho do índice, 3 seletividade, 4 lote, 5 espaço do índice, 6 tempo de
  carga, 7 CPU, 8 memória, 9 espaço em disco).

### 17.9 C2 (upsert) sem `ON CONFLICT` em todo o trabalho — **alterar o texto do TCC e justificar**

**Decisão (17/10): opção B.** A C2 do **PostgreSQL** passa a usar, em **todos** os experimentos (consulta, volume, análise de
índice e lote), um upsert **sem `ON CONFLICT`**: `UPDATE` condicionado (`WHERE id = ... AND hash <> ... AND versao >= ...`)
seguido de `INSERT ... WHERE NOT EXISTS`, escritos numa instrução só (CTE). O MongoDB não muda (`find_one` + escrita).
Código: `SQL_UPSERT_C2_PG` (individual) e `SQL_UPSERT_LOTE_C2_PG` (lote) no `benchmark.py`, e as células da C2, da Seção 7
e do lote no notebook. Testado no banco (inserção, atualização, mesmo hash, versão antiga e C2 individual).

**Quais resultados já estavam assim e quais foram refeitos.**
- [x] `C2_id` (gráficos 1 e 2): **já** usava o comando sem `ON CONFLICT` na rodada de 10 repetições. Válido.
- [x] **Lote da C2** (gráfico 4, inserção e atualização): **refeito** em 17/10 com o comando novo
  (`python benchmark.py lote --consultas C2 ...`); C8 e C3 do lote não foram refeitos (não dependem da C2).
- [x] **C2 normal por volume:** deixou de ser medida. No `benchmark.py` (e na célula do volume do notebook), C2, C3 e C8
  "normais" só existem para o **lote** (`ESCRITAS_SO_NO_LOTE`); no experimento de **volume** a escrita pontual por chave é
  medida só em `C2_id`, `C3_id` e `C8_id` (com e sem índice), porque o cenário "com índice" dessas é o mesmo comando das
  normais. A chave `C2` (que tinha o comando antigo) foi **removida** do `resultados_volume.json`; as chaves `C3` e `C8`
  continuam no arquivo (valores válidos, mas sem uso: nenhum gráfico as usa). Para o custo de uma escrita pontual usar a
  `C2_id`, a `C3_id` ou a `C8_id` com índice.

**Por que não usar o `ON CONFLICT` (justificativa para o texto).**
- [ ] O `ON CONFLICT (id) DO UPDATE` é a forma mais eficiente e idiomática de upsert no PostgreSQL (uma instrução atômica).
- [ ] Mas, para detectar o conflito, ele usa **sempre** o índice da chave primária por dentro, e os parâmetros do
  planejador (`enable_indexscan`, `enable_bitmapscan`, `enable_indexonlyscan`) **não o desligam**. O cenário "sem índice"
  continuaria usando o índice, e a análise do efeito do índice ficaria inválida.
- [ ] Como os dois bancos sempre têm o índice do id (chave primária / `_id`, que não pode ser removido), "sem índice" é
  "sem **usar** o índice": PostgreSQL com as varreduras por índice desligadas na sessão; MongoDB com `hint({$natural: 1})`.
  Para isso a C2 precisa de um comando cuja busca pelo id possa ser feita por índice ou por varredura.
- [ ] Para ter **uma única definição da C2** em todo o trabalho (e não duas versões para explicar), a mesma forma foi usada nos
  demais experimentos.

**Custo de não usar o comando mais eficiente (declarar como limitação).**
- [ ] **Escrita pontual:** o PostgreSQL fica ~5 a 7 ms (~25 a 40%) mais lento que com `ON CONFLICT` (medição da rodada
  anterior: C2 normal 15,1 a 18,1 ms contra C2_id 20,4 a 24,8 ms). Pequeno perto do efeito do índice (5× a 48×).
- [ ] **Lote:** o efeito é maior nos lotes grandes. Custo por item no PostgreSQL, inserção (antes com `ON CONFLICT` → agora):
  100 mil itens 0,080 → 0,148 ms (~1,9×); 50 mil 0,084 → 0,132 ms (~1,6×); 10 mil 0,134 → 0,181 ms (~1,4×); lotes pequenos
  (1 a 100) ~1,25× a 1,3×. Atualização: 100 mil 0,084 → 0,154 ms (~1,8×). O Mongo não mudou (0,013 e 0,051 ms/item em
  100 mil). Com isso a razão PG ÷ Mongo na inserção de 100 mil passou de ~6× para ~11,6× e na atualização de ~1,7× para ~3,1×.
  **Dizer no texto** que a comparação PostgreSQL × MongoDB no lote da C2 usa o upsert sem `ON CONFLICT` e que, com o
  `ON CONFLICT`, o PostgreSQL seria até ~1,9× mais rápido nos lotes grandes (valores da rodada anterior, 1 execução).
  Ressalva: alguns pontos oscilaram (ex.: lote de 1.000 na inserção ficou mais rápido que antes), então tratar a
  razão como ordem de grandeza.

**O que escrever no texto da C2 (para trocar o que já está escrito).**
- [ ] Descrever a C2 do PostgreSQL como `UPDATE` condicionado + `INSERT` se o item não existe (lógica "ler, decidir,
  escrever", igual à do MongoDB), e **acrescentar a justificativa acima** (por que não `ON CONFLICT`).
- [ ] Ameaça à validade: a C2 não usa a forma mais eficiente do PostgreSQL (`ON CONFLICT`); o custo medido foi ~25 a 40% na
  escrita pontual e até ~1,9× por item nos lotes grandes. Os tempos absolutos da C2 do PostgreSQL são, portanto,
  pessimistas; a análise do efeito do índice não é afetada.

**Resumo por consulta:**

| Consulta | Comando (com e sem índice) | Mudou em relação ao que era? |
|---|---|---|
| C2 | PostgreSQL: UPDATE condicionado + INSERT (sem `ON CONFLICT`); Mongo: `find_one` + escrita | **Sim, só no PostgreSQL** |
| C3 | `UPDATE ... WHERE id = ...` (PG) / `update_many` por `_id` (Mongo) | Não |
| C8 | `UPDATE ... WHERE id = ...` com `jsonb_set` (PG) / `update_many` por `_id` com `$addToSet` (Mongo) | Não |

**Notebook:** as células da C2 (Seção 6), da Seção 7 (`C2_id`) e do lote usam o comando sem `ON CONFLICT`, e as descrições
explicam o motivo. **`benchmark.py`:** `--consultas` agora vale também para o lote, e com `--consultas` o volume não repete
a carga nem sobrescreve as cargas medidas.

### 17.10 Gráficos limpos: só título, gráfico e legenda (17/10) — **a explicação passa para a legenda da figura no TCC**

Foram removidos de todos os gráficos: subtítulo, nota de rodapé e tabela de planos de execução (`graficos.py`: `moldura` só
desenha o título; funções `tabela_planos`, `figura_com_tabela`, `plano_do`, `NOTA_LEITURA` e o gráfico de escritas por volume
foram apagados). Títulos novos (sem pergunta, identificando o gráfico):

| Nº | Arquivo | Título |
|---|---|---|
| 1 | `grafico1_escala_<C>.png` | "C1 · categoria e status: tempo por volume de itens" (idem C2 a C8 por id) |
| 2 | `grafico2_ganho_indice.png` | "Ganho do índice por consulta (volume de 100 mil de itens)" |
| 3 | `grafico3_seletividade_<C>.png` | "C1 · categoria e status: seletividade (volume de 1 milhão de itens)" (C1, C4, C5, C7) |
| 4 | `grafico4_lote_<op>.png` | "C2 · upsert de itens novos: custo por item por tamanho do lote" (C2 inserção, C2 atualização, C8, C3) |
| 5 | `grafico5_espaco_indice.png` | "Espaço ocupado pelos índices (volume de 100 mil de itens)" |
| 6 | `grafico6_carga_tempo.png` | "Tempo de carga por volume" |
| 7 | `grafico7_carga_cpu.png` | "CPU durante a carga por volume" |
| 8 | `grafico8_carga_memoria.png` | "Memória durante a carga por volume" |
| 9 | `grafico9_carga_espaco.png` | "Espaço em disco dos dados por volume" |

**Legendas dentro dos gráficos** (o que sobrou de explicação): PostgreSQL/MongoDB, com/sem índice, "índice forçado" (gráficos 1 e
2), "índice ignorado (Seq Scan)" (gráfico 3, barra pontilhada), e, nos gráficos 1 e 4, "ponto: média / barra: Q1 a Q3 / fio:
mínimo a máximo"; nos gráficos 6 a 8, "fio: mínimo a máximo entre as rodadas".

**O que escrever na legenda de cada figura no TCC (era o texto do rodapé/subtítulo):**
- [ ] **Todas as de tempo (1 a 4):** "Cache do banco e do sistema operacional limpos antes de cada repetição; 10 repetições por
  medição; valores em média."
- [ ] **Gráfico 1 (C2, C3, C8 por id):** "'Sem índice' = varredura forçada da tabela (o índice do id não pode ser removido): no
  PostgreSQL, varreduras por índice desligadas na sessão; no MongoDB, `hint({$natural: 1})`." Escala logarítmica nos dois eixos.
- [ ] **Gráfico 1 (C1 e C5, 1 milhão):** a barra/ponto "índice forçado" é o PostgreSQL com `enable_seqscan = off`, medido só quando o
  planejador ignorou o índice.
- [ ] **Gráfico 2:** "Ganho = média do tempo sem índice ÷ média do tempo com índice; acima de 1 o índice ajuda, abaixo de 1
  piora." Escala logarítmica.
- [ ] **Gráfico 3:** "Parâmetro da consulta variando a quantidade de linhas devolvidas (mais linhas = menos seletiva). Barra
  pontilhada: o índice existia, mas o planejador do PostgreSQL usou varredura sequencial."
- [ ] **Gráfico 4:** "Custo por item = tempo da operação ÷ número de itens do lote. Escala logarítmica."
- [ ] **Gráficos 6 a 8:** "Média de 5 rodadas da carga, recriando os bancos do zero a cada rodada. Em 5 mil itens há só 1 a 3
  amostras do monitor de CPU/memória." Gráfico 6 também em escala logarítmica.
- [ ] **Gráfico 9:** "O MongoDB comprime os dados em disco; a comparação justa com o PostgreSQL (que não comprime a tabela) é com o
  tamanho sem compressão." Escala logarítmica.
- [ ] **Planos de execução** (a tabela que saiu dos gráficos 1 a 3): citar no texto, quando forem relevantes, os fatos já
  verificados: em 1 milhão o PostgreSQL ignorou o índice na C1 e na C5 (Seq Scan) e no gráfico 3 (C1, faixas de preço); as
  consultas por id usam Index Scan (PG) e EXPRESS_IXSCAN (Mongo) com índice, e Seq Scan (Parallel Seq Scan em 1 milhão) e COLLSCAN
  sem índice. Os planos de cada medição continuam nos JSON (campos `tipo_acesso` e `indice`).
