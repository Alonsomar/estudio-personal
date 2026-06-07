"""init: extensión pgvector + tabla chunks con índice HNSW.

El patrón de migración para despliegues reales: cada cambio de schema es una
revisión versionada con `upgrade()` (aplicar) y `downgrade()` (revertir). El
downgrade ES el plan de rollback: si la migración rompe producción, `alembic
downgrade -1` la deshace de forma determinista.

Revision ID: 0001_init
Revises: (base)

Uso (con alembic instalado y alembic.ini apuntando a DATABASE_URL):
    alembic upgrade head      # aplica
    alembic downgrade -1      # rollback de la última
"""

from alembic import op

# Identificadores de la cadena de revisiones.
revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE chunks (
            chunk_id   TEXT PRIMARY KEY,
            doc_id     TEXT NOT NULL,
            text       TEXT NOT NULL,
            embedding  vector(1536),
            metadata   JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX chunks_embedding_idx "
        "ON chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX chunks_doc_id_idx ON chunks (doc_id)")


def downgrade() -> None:
    # El rollback deshace en orden inverso. No se dropea la extensión `vector`:
    # podría estar en uso por otras tablas, y dropearla es destructivo.
    op.execute("DROP INDEX IF EXISTS chunks_doc_id_idx")
    op.execute("DROP INDEX IF EXISTS chunks_embedding_idx")
    op.execute("DROP TABLE IF EXISTS chunks")
