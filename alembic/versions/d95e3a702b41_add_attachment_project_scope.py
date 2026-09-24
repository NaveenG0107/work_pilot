"""Add and backfill project scope for task and user-story attachments.

Revision ID: d95e3a702b41
Revises: c84d2f691a30
"""

from alembic import op
import sqlalchemy as sa

revision = "d95e3a702b41"
down_revision = "c84d2f691a30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, parent, parent_id in (
        ("task_attachments", "tasks", "task_id"),
        ("user_story_attachments", "user_stories", "user_story_id"),
    ):
        op.add_column(table, sa.Column("project_id", sa.String(36), nullable=True))
        # Include soft-deleted parents so their attachment ownership is preserved.
        op.execute(sa.text(
            f"UPDATE {table} AS attachment SET project_id = parent.project_id "
            f"FROM {parent} AS parent WHERE parent.id = attachment.{parent_id}"
        ))
        op.alter_column(
            table, "project_id", existing_type=sa.String(36), nullable=False
        )
        op.create_foreign_key(
            f"fk_{table}_project_id_projects", table, "projects",
            ["project_id"], ["id"], ondelete="CASCADE",
        )
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def downgrade() -> None:
    for table in ("user_story_attachments", "task_attachments"):
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_constraint(
            f"fk_{table}_project_id_projects", table, type_="foreignkey"
        )
        op.drop_column(table, "project_id")
