"""label partial unique index on active records

Revision ID: a3e5b7d9f1c2
Revises: c72d9e4a1f5b
Create Date: 2026-09-25 17:27:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3e5b7d9f1c2"
down_revision: Union[str, None] = "c72d9e4a1f5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("idx_project_label_name", table_name="labels", if_exists=True)
    op.drop_index("idx_project_label_name_active", table_name="labels", if_exists=True)
    op.create_index(
        "idx_project_label_name",
        "labels",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_project_label_name", table_name="labels", if_exists=True)
    op.drop_index("idx_project_label_name_active", table_name="labels", if_exists=True)
    op.create_index(
        "idx_project_label_name",
        "labels",
        ["project_id", "name"],
        unique=True,
    )
