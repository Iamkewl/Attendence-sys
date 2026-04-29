"""Add governance tables for template refresh and camera drift.

Revision ID: 20260410_04
Revises: 20260410_03
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260410_04"
down_revision = "20260410_03"
branch_labels = None
depends_on = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(i["name"] == index_name for i in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "student_embeddings", "created_at"):
        op.add_column(
            "student_embeddings",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "student_embeddings", "ix_student_embeddings_created_at"):
        op.create_index(
            "ix_student_embeddings_created_at",
            "student_embeddings",
            ["created_at"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "template_audit_log"):
        op.create_table(
            "template_audit_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("student_id", sa.Integer(), nullable=False),
            sa.Column("old_embedding_id", sa.Integer(), nullable=True),
            sa.Column("new_embedding_id", sa.Integer(), nullable=True),
            sa.Column("refresh_confidence", sa.Float(), nullable=False),
            sa.Column("refresh_quality", sa.Float(), nullable=True),
            sa.Column(
                "refreshed_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("refreshed_by", sa.String(length=32), nullable=False, server_default="system"),
            sa.Column("action", sa.String(length=24), nullable=False, server_default="refresh"),
            sa.Column("rollback_of_id", sa.Integer(), nullable=True),
            sa.Column(
                "details",
                postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["new_embedding_id"], ["student_embeddings.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["old_embedding_id"], ["student_embeddings.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "template_audit_log", "ix_template_audit_log_student_id"):
        op.create_index("ix_template_audit_log_student_id", "template_audit_log", ["student_id"], unique=False)
    if not _has_index(inspector, "template_audit_log", "ix_template_audit_log_old_embedding_id"):
        op.create_index(
            "ix_template_audit_log_old_embedding_id",
            "template_audit_log",
            ["old_embedding_id"],
            unique=False,
        )
    if not _has_index(inspector, "template_audit_log", "ix_template_audit_log_new_embedding_id"):
        op.create_index(
            "ix_template_audit_log_new_embedding_id",
            "template_audit_log",
            ["new_embedding_id"],
            unique=False,
        )
    if not _has_index(inspector, "template_audit_log", "ix_template_audit_log_refreshed_at"):
        op.create_index("ix_template_audit_log_refreshed_at", "template_audit_log", ["refreshed_at"], unique=False)
    if not _has_index(inspector, "template_audit_log", "ix_template_audit_log_action"):
        op.create_index("ix_template_audit_log_action", "template_audit_log", ["action"], unique=False)
    if not _has_index(inspector, "template_audit_log", "ix_template_audit_log_rollback_of_id"):
        op.create_index(
            "ix_template_audit_log_rollback_of_id",
            "template_audit_log",
            ["rollback_of_id"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "camera_drift_events"):
        op.create_table(
            "camera_drift_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("camera_id", sa.String(length=100), nullable=False),
            sa.Column("current_rate", sa.Float(), nullable=False),
            sa.Column("baseline_rate", sa.Float(), nullable=False),
            sa.Column("drop_ratio", sa.Float(), nullable=False),
            sa.Column("threshold", sa.Float(), nullable=False),
            sa.Column(
                "detected_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column(
                "details",
                postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "camera_drift_events", "ix_camera_drift_events_camera_id"):
        op.create_index(
            "ix_camera_drift_events_camera_id",
            "camera_drift_events",
            ["camera_id"],
            unique=False,
        )
    if not _has_index(inspector, "camera_drift_events", "ix_camera_drift_events_detected_at"):
        op.create_index(
            "ix_camera_drift_events_detected_at",
            "camera_drift_events",
            ["detected_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_index(inspector, "camera_drift_events", "ix_camera_drift_events_detected_at"):
        op.drop_index("ix_camera_drift_events_detected_at", table_name="camera_drift_events")
    if _has_index(inspector, "camera_drift_events", "ix_camera_drift_events_camera_id"):
        op.drop_index("ix_camera_drift_events_camera_id", table_name="camera_drift_events")

    inspector = sa.inspect(bind)
    if _has_table(inspector, "camera_drift_events"):
        op.drop_table("camera_drift_events")

    inspector = sa.inspect(bind)
    if _has_index(inspector, "template_audit_log", "ix_template_audit_log_rollback_of_id"):
        op.drop_index("ix_template_audit_log_rollback_of_id", table_name="template_audit_log")
    if _has_index(inspector, "template_audit_log", "ix_template_audit_log_action"):
        op.drop_index("ix_template_audit_log_action", table_name="template_audit_log")
    if _has_index(inspector, "template_audit_log", "ix_template_audit_log_refreshed_at"):
        op.drop_index("ix_template_audit_log_refreshed_at", table_name="template_audit_log")
    if _has_index(inspector, "template_audit_log", "ix_template_audit_log_new_embedding_id"):
        op.drop_index("ix_template_audit_log_new_embedding_id", table_name="template_audit_log")
    if _has_index(inspector, "template_audit_log", "ix_template_audit_log_old_embedding_id"):
        op.drop_index("ix_template_audit_log_old_embedding_id", table_name="template_audit_log")
    if _has_index(inspector, "template_audit_log", "ix_template_audit_log_student_id"):
        op.drop_index("ix_template_audit_log_student_id", table_name="template_audit_log")

    inspector = sa.inspect(bind)
    if _has_table(inspector, "template_audit_log"):
        op.drop_table("template_audit_log")

    inspector = sa.inspect(bind)
    if _has_index(inspector, "student_embeddings", "ix_student_embeddings_created_at"):
        op.drop_index("ix_student_embeddings_created_at", table_name="student_embeddings")

    inspector = sa.inspect(bind)
    if _has_column(inspector, "student_embeddings", "created_at"):
        op.drop_column("student_embeddings", "created_at")
