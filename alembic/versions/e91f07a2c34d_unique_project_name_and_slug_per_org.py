"""Make project name and project slug unique within an organization.

Revision ID: e91f07a2c34d
Revises: e82f06b194ac
"""
from alembic import op
import sqlalchemy as sa


revision = "e91f07a2c34d"
down_revision = "e82f06b194ac"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Resolve existing duplicate project names within each organization before adding unique index
    op.execute("""
        WITH duplicates AS (
            SELECT id, name, ROW_NUMBER() OVER (
                PARTITION BY organization_id, lower(name)
                ORDER BY created_at, id
            ) as row_num
            FROM projects
            WHERE deleted_at IS NULL
        )
        UPDATE projects
        SET name = projects.name || ' (' || (duplicates.row_num - 1) || ')'
        FROM duplicates
        WHERE projects.id = duplicates.id AND duplicates.row_num > 1
    """)

    # 2. Drop the old global unique index on slug
    op.drop_index("idx_projects_slug", table_name="projects", if_exists=True)

    # 3. Create unique index for (organization_id, name) scoped to active projects
    op.create_index(
        "idx_projects_org_name",
        "projects",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 4. Create unique index for (organization_id, slug) scoped to active projects
    op.create_index(
        "idx_projects_org_slug",
        "projects",
        ["organization_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade():
    op.drop_index("idx_projects_org_name", table_name="projects", if_exists=True)
    op.drop_index("idx_projects_org_slug", table_name="projects", if_exists=True)
    op.create_index(
        "idx_projects_slug",
        "projects",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
