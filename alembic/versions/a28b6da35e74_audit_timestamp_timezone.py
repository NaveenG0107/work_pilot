"""Use timezone-aware audit timestamps.

Revision ID: a28b6da35e74
Revises: f17a5c924d63
"""

from alembic import op
import sqlalchemy as sa

revision = "a28b6da35e74"
down_revision = "f17a5c924d63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Interpret historical naive timestamps in the database session timezone,
    # which PostgreSQL used when storing timezone-aware application values.
    op.alter_column(
        "audit_logs", "created_at",
        existing_type=sa.DateTime(timezone=False),
        type_=sa.DateTime(timezone=True), existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE current_setting('TimeZone')",
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs", "created_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(timezone=False), existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE current_setting('TimeZone')",
    )
