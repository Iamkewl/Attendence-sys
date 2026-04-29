"""Make detections.student_id nullable for right-to-deletion anonymization.

Revision ID: 20260411_01
Revises: 20260410_04
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "20260411_01"
down_revision = "20260410_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "detections",
        "student_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # Set any NULLs before reverting to non-nullable
    op.execute("UPDATE detections SET student_id = -1 WHERE student_id IS NULL")
    op.alter_column(
        "detections",
        "student_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
