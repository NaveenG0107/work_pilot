"""Add nullable user cover image URL.

Revision ID: e82f06b194ac
Revises: da5f40c71843
"""
from alembic import op
import sqlalchemy as sa

revision = "e82f06b194ac"
down_revision = "da5f40c71843"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("cover_img_url", sa.String(500), nullable=True))


def downgrade():
    op.drop_column("users", "cover_img_url")
