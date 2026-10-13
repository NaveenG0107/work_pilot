"""Add performance indexes for tasks, comments, and sprints.

Revision ID: c72d9e4a1f5b
Revises: b51a8d4e2c9f
"""
from alembic import op
import sqlalchemy as sa


revision = "c72d9e4a1f5b"
down_revision = "b51a8d4e2c9f"
branch_labels = None
depends_on = None


def upgrade():
    # Tasks performance indexes
    op.create_index(
        "idx_tasks_proj_created_at",
        "tasks",
        ["project_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_tasks_proj_status",
        "tasks",
        ["project_id", "status_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_tasks_proj_sprint",
        "tasks",
        ["project_id", "sprint_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_tasks_proj_story",
        "tasks",
        ["project_id", "user_story_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_tasks_assignee_active",
        "tasks",
        ["assignee_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Comments performance indexes
    op.create_index(
        "idx_comments_task_created_at",
        "comments",
        ["task_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_comments_story_created_at",
        "comments",
        ["user_story_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Sprints performance indexes
    op.create_index(
        "idx_sprints_proj_created_at",
        "sprints",
        ["project_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade():
    op.drop_index("idx_sprints_proj_created_at", table_name="sprints", if_exists=True)
    op.drop_index("idx_comments_story_created_at", table_name="comments", if_exists=True)
    op.drop_index("idx_comments_task_created_at", table_name="comments", if_exists=True)
    op.drop_index("idx_tasks_assignee_active", table_name="tasks", if_exists=True)
    op.drop_index("idx_tasks_proj_story", table_name="tasks", if_exists=True)
    op.drop_index("idx_tasks_proj_sprint", table_name="tasks", if_exists=True)
    op.drop_index("idx_tasks_proj_status", table_name="tasks", if_exists=True)
    op.drop_index("idx_tasks_proj_created_at", table_name="tasks", if_exists=True)
