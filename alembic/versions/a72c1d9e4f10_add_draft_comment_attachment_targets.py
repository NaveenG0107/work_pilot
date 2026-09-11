"""add draft comment attachment targets

Revision ID: a72c1d9e4f10
Revises: 99825e94176b
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a72c1d9e4f10"
down_revision: Union[str, Sequence[str], None] = "99825e94176b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "comment_attachments",
        "comment_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )
    op.add_column(
        "comment_attachments",
        sa.Column("task_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "comment_attachments",
        sa.Column("user_story_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_comment_attachments_task_id"),
        "comment_attachments",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_comment_attachments_user_story_id"),
        "comment_attachments",
        ["user_story_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_comment_attachments_task_id_tasks",
        "comment_attachments",
        "tasks",
        ["task_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_comment_attachments_user_story_id_user_stories",
        "comment_attachments",
        "user_stories",
        ["user_story_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_comment_attachments_user_story_id_user_stories",
        "comment_attachments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_comment_attachments_task_id_tasks",
        "comment_attachments",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_comment_attachments_user_story_id"),
        table_name="comment_attachments",
    )
    op.drop_index(
        op.f("ix_comment_attachments_task_id"),
        table_name="comment_attachments",
    )
    op.drop_column("comment_attachments", "user_story_id")
    op.drop_column("comment_attachments", "task_id")
    op.alter_column(
        "comment_attachments",
        "comment_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
