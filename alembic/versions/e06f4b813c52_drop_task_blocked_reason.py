"""Remove obsolete task blocked reason, matching Go migration 036.

Revision ID: e06f4b813c52
Revises: d95e3a702b41
"""

from alembic import op
import sqlalchemy as sa

revision = "e06f4b813c52"
down_revision = "d95e3a702b41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("tasks", "blocked_reason")


def downgrade() -> None:
    # Recreates the column only; removed text cannot be restored.
    op.add_column("tasks", sa.Column("blocked_reason", sa.Text(), nullable=True))
