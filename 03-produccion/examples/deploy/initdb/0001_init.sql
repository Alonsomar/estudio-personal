-- Schema inicial del RAG fiscal. Lo corre Postgres automáticamente en el primer
-- arranque del contenedor (montado en /docker-entrypoint-initdb.d).
--
-- Para despliegues reales NO se usa este atajo: se usa alembic versionado, con
-- upgrade/downgrade (ver ../alembic/versions/0001_init.py). Este .sql es el
-- equivalente para levantar el nivel B local de un saque.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL,
    text       TEXT NOT NULL,
    embedding  vector(1536),            -- text-embedding-3-small
    metadata   JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Índice ANN para búsqueda densa por coseno (HNSW: buen recall/latencia).
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Filtro por documento (metadata filtering de 02-retrieval §7).
CREATE INDEX IF NOT EXISTS chunks_doc_id_idx ON chunks (doc_id);
