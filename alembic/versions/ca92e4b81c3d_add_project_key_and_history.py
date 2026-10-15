"""add project key and project key history

Revision ID: ca92e4b81c3d
Revises: b4c7e53a9fae
Create Date: 2026-10-15 20:25:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "ca92e4b81c3d"
down_revision: Union[str, Sequence[str], None] = "b4c7e53a9fae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add key column to projects table as nullable first for backfill
    op.add_column(
        "projects",
        sa.Column("key", sa.String(length=20), nullable=True),
    )

    # 2. Backfill existing projects safely:
    # Use existing task key prefix if available, otherwise first 3 alphanumeric chars of slug/name.
    # Deterministically resolve duplicates within each organization using ROW_NUMBER.
    op.execute(
        """
        WITH task_prefixes AS (
            SELECT
                project_id,
                SPLIT_PART(key, '-', 1) AS task_prefix,
                ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY sequence_number DESC, id) AS rn
            FROM tasks
            WHERE deleted_at IS NULL AND key LIKE '%-%'
        ),
        project_base_keys AS (
            SELECT
                p.id,
                p.organization_id,
                p.created_at,
                UPPER(COALESCE(
                    NULLIF(tp.task_prefix, ''),
                    NULLIF(SUBSTRING(REGEXP_REPLACE(UPPER(COALESCE(p.slug, p.name, 'WP')), '[^A-Z0-9]', '', 'g') FROM 1 FOR 3), ''),
                    'WP'
                )) AS base_key
            FROM projects p
            LEFT JOIN task_prefixes tp ON tp.project_id = p.id AND tp.rn = 1
        ),
        numbered_keys AS (
            SELECT
                id,
                organization_id,
                CASE
                    WHEN length(base_key) < 2 THEN 'WP'
                    ELSE SUBSTRING(base_key FROM 1 FOR 10)
                END AS base_key,
                ROW_NUMBER() OVER (
                    PARTITION BY organization_id, base_key
                    ORDER BY created_at, id
                ) AS row_num
            FROM project_base_keys
        )
        UPDATE projects p
        SET key = CASE
            WHEN nk.row_num = 1 THEN nk.base_key
            ELSE SUBSTRING(nk.base_key FROM 1 FOR 7) || nk.row_num::text
        END
        FROM numbered_keys nk
        WHERE p.id = nk.id;
        """
    )

    # Fallback for any records still null or too short
    op.execute(
        """
        UPDATE projects
        SET key = 'WP'
        WHERE key IS NULL OR length(key) < 2;
        """
    )

    # 3. Alter key column to nullable=False after backfill
    op.alter_column(
        "projects",
        "key",
        nullable=False,
    )

    # 4. Create unique partial index on (organization_id, key) for active projects
    op.create_index(
        "idx_projects_org_key",
        "projects",
        ["organization_id", "key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 5. Create project_key_history table
    op.create_table(
        "project_key_history",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("projects.id", ondelete="CASCADE", name="fk_project_key_history_project"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", name="fk_project_key_history_org"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "changed_by",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", name="fk_project_key_history_user"),
            nullable=True,
        ),
    )

    op.create_index(
        "idx_project_key_history_project_id",
        "project_key_history",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "idx_project_key_history_org_key",
        "project_key_history",
        ["organization_id", "key"],
        unique=True,
    )
    op.create_index(
        "idx_project_key_history_proj_key",
        "project_key_history",
        ["project_id", "key"],
        unique=True,
    )


def downgrade() -> None:
    # 1. Drop project_key_history table and indexes
    op.drop_index("idx_project_key_history_proj_key", table_name="project_key_history", if_exists=True)
    op.drop_index("idx_project_key_history_org_key", table_name="project_key_history", if_exists=True)
    op.drop_index("idx_project_key_history_project_id", table_name="project_key_history", if_exists=True)
    op.drop_table("project_key_history")

    # 2. Drop idx_projects_org_key index
    op.drop_index(
        "idx_projects_org_key",
        table_name="projects",
        if_exists=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 3. Drop projects.key column
    op.drop_column("projects", "key")
