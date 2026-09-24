"""Correct quoted empty defaults and remove user boolean database defaults.

Revision ID: c94e3fb60732
Revises: b83d2ea5f621
"""
from alembic import op
import sqlalchemy as sa

revision = "c94e3fb60732"
down_revision = "b83d2ea5f621"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("task_attachments", "user_story_attachments"):
        op.alter_column(table, "url", server_default=sa.text("''::text"))
    op.alter_column("user_stories", "key", server_default=sa.text("''::character varying"))
    op.alter_column("users", "is_active", server_default=None)
    op.alter_column("users", "is_verified", server_default=None)


def downgrade():
    # Restore the literal defaults from revision 0001, including its quoting bug.
    for table in ("task_attachments", "user_story_attachments"):
        op.alter_column(table, "url", server_default="'::text")
    op.alter_column("user_stories", "key", server_default="'::character varying")
    op.alter_column("users", "is_active", server_default=sa.text("true"))
    op.alter_column("users", "is_verified", server_default=sa.text("false"))
