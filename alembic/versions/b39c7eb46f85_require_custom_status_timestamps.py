"""Require custom-status creation and update timestamps.

Revision ID: b39c7eb46f85
Revises: a28b6da35e74
"""

from alembic import op
import sqlalchemy as sa

revision = "b39c7eb46f85"
down_revision = "a28b6da35e74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve known timestamps. If both are missing, use the migration time.
    op.execute("""
        UPDATE custom_statuses
        SET created_at = COALESCE(created_at, updated_at, CURRENT_TIMESTAMP),
            updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
        WHERE created_at IS NULL OR updated_at IS NULL
    """)
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "custom_statuses", column,
            existing_type=sa.DateTime(timezone=True), nullable=False,
        )


def downgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "custom_statuses", column,
            existing_type=sa.DateTime(timezone=True), nullable=True,
        )
