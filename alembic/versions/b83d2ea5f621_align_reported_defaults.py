"""Align reported defaults and active user-story key uniqueness with Go backup.

Revision ID: b83d2ea5f621
Revises: a72c1d9e4f10
"""
from alembic import op
import sqlalchemy as sa

revision = "b83d2ea5f621"
down_revision = "a72c1d9e4f10"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("audit_logs", "type", server_default=sa.text("'activity'"))
    op.alter_column("comments", "is_deleted", server_default=None)
    for column in ("created_at", "updated_at"):
        op.alter_column("countries", column, server_default=sa.text("now()"))
    op.alter_column("organization_invitations", "status", server_default=sa.text("'pending'"))
    for column in ("remaining_story_points", "total_story_points"):
        op.alter_column("sprint_snapshots", column, server_default=sa.text("0"))
    op.alter_column("comment_attachments", "url", server_default=sa.text("''::text"))
    op.drop_index("idx_project_user_story_key", table_name="user_stories")
    op.create_index("idx_project_user_story_key", "user_stories", ["project_id", "key"],
                    unique=True, postgresql_where=sa.text("deleted_at IS NULL"))


def downgrade():
    # Reinstating unconditional uniqueness may fail if deleted keys were reused.
    # PostgreSQL rolls back the transaction in that case; no rows are removed.
    op.drop_index("idx_project_user_story_key", table_name="user_stories")
    op.create_index("idx_project_user_story_key", "user_stories", ["project_id", "key"], unique=True)
    op.alter_column("comment_attachments", "url", server_default="'::text")
    for column in ("remaining_story_points", "total_story_points"):
        op.alter_column("sprint_snapshots", column, server_default=None)
    op.alter_column("organization_invitations", "status", server_default=None)
    for column in ("created_at", "updated_at"):
        op.alter_column("countries", column, server_default=None)
    op.alter_column("comments", "is_deleted", server_default=sa.text("false"))
    op.alter_column("audit_logs", "type", server_default=None)
