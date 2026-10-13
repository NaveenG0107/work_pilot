"""sprint_dates_timestamptz

Revision ID: b4c7e53a9fae
Revises: a3e5b7d9f1c2
Create Date: 2026-10-13 12:06:57.668727

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c7e53a9fae'
down_revision: Union[str, Sequence[str], None] = 'a3e5b7d9f1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "sprints",
        "start_date",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.Date(),
        existing_nullable=True,
        postgresql_using="start_date::timestamp with time zone",
    )
    op.alter_column(
        "sprints",
        "end_date",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.Date(),
        existing_nullable=True,
        postgresql_using="end_date::timestamp with time zone",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "sprints",
        "start_date",
        type_=sa.Date(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="start_date::date",
    )
    op.alter_column(
        "sprints",
        "end_date",
        type_=sa.Date(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="end_date::date",
    )
