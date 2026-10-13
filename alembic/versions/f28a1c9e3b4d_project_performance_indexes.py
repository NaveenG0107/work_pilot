"""Add performance indexes for project and project_member queries.

Revision ID: f28a1c9e3b4d
Revises: e91f07a2c34d
"""
from alembic import op
import sqlalchemy as sa


revision = "f28a1c9e3b4d"
down_revision = "e91f07a2c34d"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Project filtering and sorting indexes
    op.create_index(
        "idx_projects_org_status",
        "projects",
        ["organization_id", "status"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_projects_org_created_at",
        "projects",
        ["organization_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 2. ProjectMember foreign key & membership lookup indexes
    op.create_index(
        "idx_project_members_project_id",
        "project_members",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "idx_project_members_user_id",
        "project_members",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "idx_project_members_proj_user",
        "project_members",
        ["project_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade():
    op.drop_index("idx_project_members_proj_user", table_name="project_members", if_exists=True)
    op.drop_index("idx_project_members_user_id", table_name="project_members", if_exists=True)
    op.drop_index("idx_project_members_project_id", table_name="project_members", if_exists=True)
    op.drop_index("idx_projects_org_created_at", table_name="projects", if_exists=True)
    op.drop_index("idx_projects_org_status", table_name="projects", if_exists=True)
