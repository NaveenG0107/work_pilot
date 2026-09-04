"""add user story keys and project sequence numbers

Revision ID: c84d2f691a30
Revises: a72c1d9e4f10
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c84d2f691a30"
down_revision: Union[str, Sequence[str], None] = "a72c1d9e4f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_stories",
        sa.Column("key", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "user_stories",
        sa.Column("sequence_number", sa.Integer(), nullable=True),
    )

    op.execute(
        """
        WITH numbered_stories AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY project_id
                    ORDER BY created_at, id
                ) AS sequence_number
            FROM user_stories
        )
        UPDATE user_stories AS story
        SET
            sequence_number = numbered.sequence_number,
            key = 'US-' || numbered.sequence_number
        FROM numbered_stories AS numbered
        WHERE story.id = numbered.id
        """
    )

    op.create_index(
        "ix_user_stories_sequence_number",
        "user_stories",
        ["sequence_number"],
        unique=False,
    )
    op.create_index(
        "idx_project_user_story_key",
        "user_stories",
        ["project_id", "key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_project_user_story_key",
        table_name="user_stories",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "ix_user_stories_sequence_number",
        table_name="user_stories",
    )
    op.drop_column("user_stories", "sequence_number")
    op.drop_column("user_stories", "key")
