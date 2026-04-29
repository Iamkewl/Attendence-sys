"""Add nullable track_id to detections for temporal tracking.

Revision ID: 20260410_01
Revises: 20260408_01
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260410_01"
down_revision = "20260408_01"
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(i["name"] == index_name for i in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "detections"

    if not _has_column(inspector, table_name, "track_id"):
        op.add_column(table_name, sa.Column("track_id", sa.Integer(), nullable=True))

    inspector = sa.inspect(bind)
    if not _has_index(inspector, table_name, "ix_detections_track_id"):
        op.create_index("ix_detections_track_id", table_name, ["track_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "detections"

    if _has_index(inspector, table_name, "ix_detections_track_id"):
        op.drop_index("ix_detections_track_id", table_name=table_name)

    inspector = sa.inspect(bind)
    if _has_column(inspector, table_name, "track_id"):
        op.drop_column(table_name, "track_id")
