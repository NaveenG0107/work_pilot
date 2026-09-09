"""Correct quoted empty defaults and remove user boolean database defaults.

Revision ID: 0003_fix_remaining_defaults
Revises: 0002_align_reported_defaults
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_fix_remaining_defaults"
down_revision = "0002_align_reported_defaults"
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
