"""Add the country-name trigram index from Go migration 005.

Revision ID: da5f40c71843
Revises: c94e3fb60732
"""
from alembic import op
import sqlalchemy as sa

revision = "da5f40c71843"
down_revision = "c94e3fb60732"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))
    op.drop_index("idx_countries_name_trgm", table_name="countries", if_exists=True)
    op.create_index(
        "idx_countries_name_trgm", "countries", ["name"],
        postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"},
    )


def downgrade():
    op.drop_index("idx_countries_name_trgm", table_name="countries")
    # pg_trgm is shared and was already installed by the initial migration.
