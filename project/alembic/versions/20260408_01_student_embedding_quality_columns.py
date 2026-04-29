"""Add enrollment quality and template lifecycle columns to student_embeddings.

Revision ID: 20260408_01
Revises:
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260408_01"
down_revision = None
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(i["name"] == index_name for i in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "student_embeddings"

    if not _has_column(inspector, table_name, "capture_quality_score"):
        op.add_column(
            table_name,
            sa.Column("capture_quality_score", sa.Float(), nullable=True),
        )
    if not _has_column(inspector, table_name, "sharpness"):
        op.add_column(table_name, sa.Column("sharpness", sa.Float(), nullable=True))
    if not _has_column(inspector, table_name, "face_size_px"):
        op.add_column(table_name, sa.Column("face_size_px", sa.Integer(), nullable=True))
    if not _has_column(inspector, table_name, "face_area_ratio"):
        op.add_column(table_name, sa.Column("face_area_ratio", sa.Float(), nullable=True))
    if not _has_column(inspector, table_name, "embedding_norm"):
        op.add_column(table_name, sa.Column("embedding_norm", sa.Float(), nullable=True))
    if not _has_column(inspector, table_name, "novelty_score"):
        op.add_column(table_name, sa.Column("novelty_score", sa.Float(), nullable=True))
    if not _has_column(inspector, table_name, "collision_risk"):
        op.add_column(table_name, sa.Column("collision_risk", sa.Float(), nullable=True))
    if not _has_column(inspector, table_name, "retention_score"):
        op.add_column(table_name, sa.Column("retention_score", sa.Float(), nullable=True))
    if not _has_column(inspector, table_name, "template_status"):
        op.add_column(
            table_name,
            sa.Column(
                "template_status",
                sa.String(length=20),
                nullable=False,
                server_default="active",
            ),
        )
    if not _has_column(inspector, table_name, "is_active"):
        op.add_column(
            table_name,
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )

    # Refresh inspector after column changes.
    inspector = sa.inspect(bind)

    if not _has_index(inspector, table_name, "ix_student_embeddings_template_status"):
        op.create_index(
            "ix_student_embeddings_template_status",
            table_name,
            ["template_status"],
            unique=False,
        )
    if not _has_index(inspector, table_name, "ix_student_embeddings_is_active"):
        op.create_index(
            "ix_student_embeddings_is_active",
            table_name,
            ["is_active"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "student_embeddings"

    if _has_index(inspector, table_name, "ix_student_embeddings_is_active"):
        op.drop_index("ix_student_embeddings_is_active", table_name=table_name)
    if _has_index(inspector, table_name, "ix_student_embeddings_template_status"):
        op.drop_index("ix_student_embeddings_template_status", table_name=table_name)

    inspector = sa.inspect(bind)
    if _has_column(inspector, table_name, "is_active"):
        op.drop_column(table_name, "is_active")
    if _has_column(inspector, table_name, "template_status"):
        op.drop_column(table_name, "template_status")
    if _has_column(inspector, table_name, "retention_score"):
        op.drop_column(table_name, "retention_score")
    if _has_column(inspector, table_name, "collision_risk"):
        op.drop_column(table_name, "collision_risk")
    if _has_column(inspector, table_name, "novelty_score"):
        op.drop_column(table_name, "novelty_score")
    if _has_column(inspector, table_name, "embedding_norm"):
        op.drop_column(table_name, "embedding_norm")
    if _has_column(inspector, table_name, "face_area_ratio"):
        op.drop_column(table_name, "face_area_ratio")
    if _has_column(inspector, table_name, "face_size_px"):
        op.drop_column(table_name, "face_size_px")
    if _has_column(inspector, table_name, "sharpness"):
        op.drop_column(table_name, "sharpness")
    if _has_column(inspector, table_name, "capture_quality_score"):
        op.drop_column(table_name, "capture_quality_score")
