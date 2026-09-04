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
  disponivel            BOOLEAN NOT NULL DEFAULT true,
  preco                 NUMERIC(10,2) NOT NULL,
  atributos_variaveis   JSONB NOT NULL DEFAULT '{}'::jsonb,
  criado_em             TIMESTAMP NOT NULL DEFAULT now(),
  atualizado_em         TIMESTAMP NOT NULL DEFAULT now(),

  CONSTRAINT pk_item PRIMARY KEY (id),
  CONSTRAINT fk_item_restaurante FOREIGN KEY (restaurante_id)
    REFERENCES restaurantes(id) ON DELETE CASCADE
);