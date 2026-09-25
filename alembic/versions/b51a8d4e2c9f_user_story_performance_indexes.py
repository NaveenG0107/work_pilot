"""Add performance indexes for user_stories queries.

Revision ID: b51a8d4e2c9f
Revises: f28a1c9e3b4d
"""
from alembic import op
import sqlalchemy as sa


revision = "b51a8d4e2c9f"
down_revision = "f28a1c9e3b4d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "idx_user_stories_proj_created_at",
        "user_stories",
        ["project_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_user_stories_proj_status",
        "user_stories",
        ["project_id", "status_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_user_stories_proj_sprint",
        "user_stories",
        ["project_id", "sprint_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_user_stories_proj_backlog",
        "user_stories",
        ["project_id", "backlog_order"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade():
    op.drop_index("idx_user_stories_proj_backlog", table_name="user_stories", if_exists=True)
    op.drop_index("idx_user_stories_proj_sprint", table_name="user_stories", if_exists=True)
    op.drop_index("idx_user_stories_proj_status", table_name="user_stories", if_exists=True)
    op.drop_index("idx_user_stories_proj_created_at", table_name="user_stories", if_exists=True)
