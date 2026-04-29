"""Add vectorscale extension and DiskANN index for student embeddings.

Revision ID: 20260410_03
Revises: 20260410_02
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260410_03"
down_revision = "20260410_02"
branch_labels = None
depends_on = None


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(i["name"] == index_name for i in inspector.get_indexes(table_name))


def _diskann_access_method_exists(bind: sa.Connection) -> bool:
    query = sa.text("SELECT EXISTS (SELECT 1 FROM pg_am WHERE amname = 'diskann')")
    return bool(bind.execute(query).scalar())


def upgrade() -> None:
    bind = op.get_bind()

    # Ensure pgvector remains present for vector type/operator usage.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # vectorscale may not be installed in all local/dev images.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vectorscale') THEN
                CREATE EXTENSION IF NOT EXISTS vectorscale;
            END IF;
        END
        $$;
        """
    )

    inspector = sa.inspect(bind)
    table_name = "student_embeddings"
    index_name = "idx_embeddings_diskann"

    if not _has_index(inspector, table_name, index_name) and _diskann_access_method_exists(bind):
        op.create_index(
            index_name,
            table_name,
            ["embedding"],
            unique=False,
            postgresql_using="diskann",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "student_embeddings"
    index_name = "idx_embeddings_diskann"

    if _has_index(inspector, table_name, index_name):
        op.drop_index(index_name, table_name=table_name)
