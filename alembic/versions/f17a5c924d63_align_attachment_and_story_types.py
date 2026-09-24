"""Align attachment nullability, task hours, and story identifier constraints.

Revision ID: f17a5c924d63
Revises: e06f4b813c52
"""

from alembic import op
import sqlalchemy as sa

revision = "f17a5c924d63"
down_revision = "e06f4b813c52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, column in (
        ("task_attachments", "task_id"),
        ("user_story_attachments", "user_story_id"),
    ):
        op.alter_column(table, column, existing_type=sa.String(36), nullable=True)
    for column in ("estimated_hours", "actual_hours"):
        op.alter_column(
            "tasks", column, existing_type=sa.Float(), type_=sa.Numeric(),
            postgresql_using=f"{column}::numeric",
        )
    # Preserve existing identifiers; allocate missing counters above each
    # project's current maximum, including soft-deleted stories.
    op.execute("""
        WITH maxima AS (
            SELECT project_id, COALESCE(MAX(sequence_number), 0) AS maximum
            FROM user_stories GROUP BY project_id
        ), missing AS (
            SELECT s.id, m.maximum + ROW_NUMBER() OVER (
                PARTITION BY s.project_id ORDER BY s.created_at, s.id
            ) AS seq
            FROM user_stories s JOIN maxima m ON m.project_id = s.project_id
            WHERE s.sequence_number IS NULL
        )
        UPDATE user_stories s SET sequence_number = m.seq
        FROM missing m WHERE s.id = m.id
    """)
    op.execute("""
        UPDATE user_stories SET key = 'US-' || sequence_number
        WHERE key IS NULL OR key = ''
    """)
    op.alter_column(
        "user_stories", "key", existing_type=sa.String(50),
        nullable=False, server_default="",
    )
    op.alter_column(
        "user_stories", "sequence_number", existing_type=sa.Integer(),
        nullable=False, server_default="0",
    )


def downgrade() -> None:
    # Fail safely if draft attachments exist; never delete them to downgrade.
    for table, column in (
        ("task_attachments", "task_id"),
        ("user_story_attachments", "user_story_id"),
    ):
        op.alter_column(table, column, existing_type=sa.String(36), nullable=False)
    for column in ("estimated_hours", "actual_hours"):
        op.alter_column(
            "tasks", column, existing_type=sa.Numeric(), type_=sa.Float(),
            postgresql_using=f"{column}::double precision",
        )
    op.alter_column(
        "user_stories", "key", existing_type=sa.String(50),
        nullable=True, server_default=None,
    )
    op.alter_column(
        "user_stories", "sequence_number", existing_type=sa.Integer(),
        nullable=True, server_default=None,
    )
