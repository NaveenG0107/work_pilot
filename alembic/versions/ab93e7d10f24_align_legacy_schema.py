"""Align the original nine-migration schema with the former consolidated initial schema.

Revision ID: ab93e7d10f24
Revises: b39c7eb46f85

Static, schema-only bridge. PostgreSQL casts and constraints fail transactionally
if existing data cannot satisfy the destination schema; no rows are deleted.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ab93e7d10f24"
down_revision = "b39c7eb46f85"
branch_labels = None
depends_on = None


def _drop_constraint(name, table, type_):
    inspector = sa.inspect(op.get_bind())
    constraints = (
        inspector.get_foreign_keys(table) if type_ == "foreignkey"
        else inspector.get_unique_constraints(table)
    )
    if not any(c["name"] == name for c in constraints):
        # These revisions used explicit fk_<table>_<column>_<target>
        # names; some legacy databases instead have PostgreSQL *_fkey names.
        explicit_targets = {
            "fk_comment_attachments_task_id_tasks": ("task_id", "tasks"),
            "fk_comment_attachments_user_story_id_user_stories": ("user_story_id", "user_stories"),
            "fk_task_attachments_project_id_projects": ("project_id", "projects"),
            "fk_user_story_attachments_project_id_projects": ("project_id", "projects"),
        }
        suffix = "_fkey" if type_ == "foreignkey" else "_key"
        column = str(name).removeprefix(table + "_").removesuffix(suffix)
        target = None
        if type_ == "foreignkey" and name in explicit_targets:
            column, target = explicit_targets[name]
        matches = [c for c in constraints if c.get("constrained_columns", c.get("column_names")) == [column]]
        if target is not None:
            matches = [c for c in matches if c["referred_table"] == target and c["referred_columns"] == ["id"]]
        if len(matches) != 1:
            raise RuntimeError(f"Cannot resolve historical constraint {table}.{name}")
        name = matches[0]["name"]
    op.drop_constraint(op.f(name), table, type_=type_)


def _replace_index(name, table_name, columns, **kwargs):
    # Rebuild only the explicitly targeted index; never skip checking its
    # definition merely because its name exists. No CASCADE is used.
    if any(index["name"] == name for index in sa.inspect(op.get_bind()).get_indexes(table_name)):
        op.drop_index(op.f(name), table_name=table_name)
    op.create_index(name, table_name, columns, **kwargs)


def upgrade():
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS global_work_item_serial_seq START WITH 1 INCREMENT BY 1'))
    op.execute(sa.text("""
        SELECT setval('global_work_item_serial_seq', GREATEST(
            (SELECT last_value FROM global_work_item_serial_seq),
            COALESCE((SELECT MAX(serial_number) FROM tasks), 0),
            COALESCE((SELECT MAX(serial_number) FROM user_stories), 0), 1
        ), (SELECT is_called FROM global_work_item_serial_seq)
           OR EXISTS (SELECT 1 FROM tasks WHERE serial_number > 0)
           OR EXISTS (SELECT 1 FROM user_stories WHERE serial_number > 0))
    """))
    for column in ("iso2", "iso3"):
        constraints = sa.inspect(op.get_bind()).get_unique_constraints("countries")
        existing = next(c["name"] for c in constraints if c["column_names"] == [column])
        desired = f"countries_{column}_key"
        if existing != desired:
            quote = op.get_bind().dialect.identifier_preparer.quote
            op.execute(sa.text(f"ALTER TABLE countries RENAME CONSTRAINT {quote(existing)} TO {quote(desired)}"))
    _drop_constraint('roles_organization_id_fkey', 'roles', type_='foreignkey')
    _drop_constraint('organization_invitations_organization_id_fkey', 'organization_invitations', type_='foreignkey')
    _drop_constraint('organization_invitations_role_id_fkey', 'organization_invitations', type_='foreignkey')
    _drop_constraint('role_permissions_permission_id_fkey', 'role_permissions', type_='foreignkey')
    _drop_constraint('role_permissions_role_id_fkey', 'role_permissions', type_='foreignkey')
    _drop_constraint('users_organization_id_fkey', 'users', type_='foreignkey')
    _drop_constraint('users_role_id_fkey', 'users', type_='foreignkey')
    _drop_constraint('projects_created_by_fkey', 'projects', type_='foreignkey')
    _drop_constraint('projects_organization_id_fkey', 'projects', type_='foreignkey')
    _drop_constraint('refresh_tokens_user_id_fkey', 'refresh_tokens', type_='foreignkey')
    _drop_constraint('custom_statuses_project_id_fkey', 'custom_statuses', type_='foreignkey')
    _drop_constraint('labels_project_id_fkey', 'labels', type_='foreignkey')
    _drop_constraint('project_members_added_by_id_fkey', 'project_members', type_='foreignkey')
    _drop_constraint('project_members_project_id_fkey', 'project_members', type_='foreignkey')
    _drop_constraint('project_members_role_id_fkey', 'project_members', type_='foreignkey')
    _drop_constraint('project_members_user_id_fkey', 'project_members', type_='foreignkey')
    _drop_constraint('sprints_created_by_id_fkey', 'sprints', type_='foreignkey')
    _drop_constraint('sprints_project_id_fkey', 'sprints', type_='foreignkey')
    _drop_constraint('user_story_statuses_project_id_fkey', 'user_story_statuses', type_='foreignkey')
    _drop_constraint('sprint_snapshots_sprint_id_fkey', 'sprint_snapshots', type_='foreignkey')
    _drop_constraint('comments_parent_comment_id_fkey', 'comments', type_='foreignkey')
    _drop_constraint('comments_project_id_fkey', 'comments', type_='foreignkey')
    _drop_constraint('comments_task_id_fkey', 'comments', type_='foreignkey')
    _drop_constraint('comments_user_id_fkey', 'comments', type_='foreignkey')
    _drop_constraint('comments_user_story_id_fkey', 'comments', type_='foreignkey')
    _drop_constraint('favorites_task_id_fkey', 'favorites', type_='foreignkey')
    _drop_constraint('favorites_user_id_fkey', 'favorites', type_='foreignkey')
    _drop_constraint('favorites_user_story_id_fkey', 'favorites', type_='foreignkey')
    _drop_constraint('task_labels_label_id_fkey', 'task_labels', type_='foreignkey')
    _drop_constraint('task_labels_task_id_fkey', 'task_labels', type_='foreignkey')
    _drop_constraint('comment_attachments_comment_id_fkey', 'comment_attachments', type_='foreignkey')
    _drop_constraint('comment_attachments_uploaded_by_fkey', 'comment_attachments', type_='foreignkey')
    _drop_constraint('fk_comment_attachments_task_id_tasks', 'comment_attachments', type_='foreignkey')
    _drop_constraint('fk_comment_attachments_user_story_id_user_stories', 'comment_attachments', type_='foreignkey')
    _drop_constraint('user_stories_assignee_id_fkey', 'user_stories', type_='foreignkey')
    _drop_constraint('user_stories_project_id_fkey', 'user_stories', type_='foreignkey')
    _drop_constraint('user_stories_reporter_id_fkey', 'user_stories', type_='foreignkey')
    _drop_constraint('user_stories_sprint_id_fkey', 'user_stories', type_='foreignkey')
    _drop_constraint('user_stories_status_id_fkey', 'user_stories', type_='foreignkey')
    _drop_constraint('fk_task_attachments_project_id_projects', 'task_attachments', type_='foreignkey')
    _drop_constraint('task_attachments_task_id_fkey', 'task_attachments', type_='foreignkey')
    _drop_constraint('task_attachments_uploaded_by_fkey', 'task_attachments', type_='foreignkey')
    _drop_constraint('fk_user_story_attachments_project_id_projects', 'user_story_attachments', type_='foreignkey')
    _drop_constraint('user_story_attachments_uploaded_by_fkey', 'user_story_attachments', type_='foreignkey')
    _drop_constraint('user_story_attachments_user_story_id_fkey', 'user_story_attachments', type_='foreignkey')
    _drop_constraint('tasks_assignee_id_fkey', 'tasks', type_='foreignkey')
    _drop_constraint('tasks_project_id_fkey', 'tasks', type_='foreignkey')
    _drop_constraint('tasks_reporter_id_fkey', 'tasks', type_='foreignkey')
    _drop_constraint('tasks_sprint_id_fkey', 'tasks', type_='foreignkey')
    _drop_constraint('tasks_user_story_id_fkey', 'tasks', type_='foreignkey')
    _drop_constraint('audit_logs_project_id_fkey', 'audit_logs', type_='foreignkey')
    _drop_constraint('audit_logs_sprint_id_fkey', 'audit_logs', type_='foreignkey')
    _drop_constraint('audit_logs_task_id_fkey', 'audit_logs', type_='foreignkey')
    _drop_constraint('audit_logs_user_id_fkey', 'audit_logs', type_='foreignkey')
    _drop_constraint('audit_logs_user_story_id_fkey', 'audit_logs', type_='foreignkey')
    op.alter_column('audit_logs', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('audit_logs', 'user_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('audit_logs', 'organization_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"organization_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('audit_logs', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('audit_logs', 'task_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"task_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('audit_logs', 'sprint_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"sprint_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('audit_logs', 'user_story_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_story_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('audit_logs', 'type',
               existing_type=sa.VARCHAR(length=50),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_organization_id'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_project_id'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_resource_type'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_sprint_id'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_task_id'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_type'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs', if_exists=True)
    op.drop_index(op.f('ix_audit_logs_user_story_id'), table_name='audit_logs', if_exists=True)
    _replace_index('idx_audit_logs_action', 'audit_logs', ['action'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_org_id', 'audit_logs', ['organization_id'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_project_id', 'audit_logs', ['project_id'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_resource_type', 'audit_logs', ['resource_type'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_sprint_id', 'audit_logs', ['sprint_id'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_task_id', 'audit_logs', ['task_id'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_type', 'audit_logs', ['type'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_user_id', 'audit_logs', ['user_id'], unique=False, postgresql_include=[])
    _replace_index('idx_audit_logs_user_story_id', 'audit_logs', ['user_story_id'], unique=False, postgresql_include=[])
    op.alter_column('comment_attachments', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('comment_attachments', 'comment_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"comment_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('comment_attachments', 'task_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"task_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('comment_attachments', 'user_story_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_story_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('comment_attachments', 'url',
               existing_type=sa.TEXT(),
               server_default=sa.text("'''::text'::text"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('comment_attachments', 'uploaded_by',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"uploaded_by"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.drop_index(op.f('ix_comment_attachments_comment_id'), table_name='comment_attachments', if_exists=True)
    op.drop_index(op.f('ix_comment_attachments_task_id'), table_name='comment_attachments', if_exists=True)
    op.drop_index(op.f('ix_comment_attachments_uploaded_by'), table_name='comment_attachments', if_exists=True)
    op.drop_index(op.f('ix_comment_attachments_user_story_id'), table_name='comment_attachments', if_exists=True)
    _replace_index('idx_comment_attachments_comment_id', 'comment_attachments', ['comment_id'], unique=False, postgresql_include=[])
    _replace_index('idx_comment_attachments_task_id', 'comment_attachments', ['task_id'], unique=False, postgresql_include=[])
    _replace_index('idx_comment_attachments_uploaded_by', 'comment_attachments', ['uploaded_by'], unique=False, postgresql_include=[])
    _replace_index('idx_comment_attachments_user_story_id', 'comment_attachments', ['user_story_id'], unique=False, postgresql_include=[])
    op.alter_column('comments', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('comments', 'task_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"task_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('comments', 'user_story_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_story_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('comments', 'user_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('comments', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('comments', 'organization_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"organization_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('comments', 'parent_comment_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"parent_comment_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('comments', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('comments', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('comments', 'is_deleted',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_comments_deleted_at'), table_name='comments', if_exists=True)
    op.drop_index(op.f('ix_comments_organization_id'), table_name='comments', if_exists=True)
    op.drop_index(op.f('ix_comments_parent_comment_id'), table_name='comments', if_exists=True)
    op.drop_index(op.f('ix_comments_project_id'), table_name='comments', if_exists=True)
    op.drop_index(op.f('ix_comments_task_id'), table_name='comments', if_exists=True)
    op.drop_index(op.f('ix_comments_user_id'), table_name='comments', if_exists=True)
    op.drop_index(op.f('ix_comments_user_story_id'), table_name='comments', if_exists=True)
    _replace_index('idx_comments_deleted_at', 'comments', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_comments_organization_id', 'comments', ['organization_id'], unique=False, postgresql_include=[])
    _replace_index('idx_comments_parent_comment_id', 'comments', ['parent_comment_id'], unique=False, postgresql_include=[])
    _replace_index('idx_comments_project_id', 'comments', ['project_id'], unique=False, postgresql_include=[])
    _replace_index('idx_comments_task_id', 'comments', ['task_id'], unique=False, postgresql_include=[])
    _replace_index('idx_comments_user_id', 'comments', ['user_id'], unique=False, postgresql_include=[])
    _replace_index('idx_comments_user_story_id', 'comments', ['user_story_id'], unique=False, postgresql_include=[])
    op.alter_column('countries', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('countries', 'iso2',
               existing_type=sa.VARCHAR(length=2),
               type_=sa.CHAR(length=2),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('countries', 'iso3',
               existing_type=sa.VARCHAR(length=3),
               type_=sa.CHAR(length=3),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('countries', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               autoincrement=False)
    op.alter_column('custom_statuses', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('custom_statuses', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('custom_statuses', 'is_default',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('custom_statuses', 'is_final',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('custom_statuses', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('custom_statuses', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_custom_statuses_deleted_at'), table_name='custom_statuses', if_exists=True)
    op.drop_index(op.f('ix_custom_statuses_project_id'), table_name='custom_statuses', if_exists=True)
    _replace_index('idx_custom_statuses_deleted_at', 'custom_statuses', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_custom_statuses_project_id', 'custom_statuses', ['project_id'], unique=False, postgresql_include=[])
    op.alter_column('favorites', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('favorites', 'user_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('favorites', 'user_story_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_story_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('favorites', 'task_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"task_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('favorites', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('favorites', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_favorites_deleted_at'), table_name='favorites', if_exists=True)
    op.drop_index(op.f('ix_favorites_task_id'), table_name='favorites', if_exists=True)
    op.drop_index(op.f('ix_favorites_user_id'), table_name='favorites', if_exists=True)
    op.drop_index(op.f('ix_favorites_user_story_id'), table_name='favorites', if_exists=True)
    _replace_index('idx_favorites_deleted_at', 'favorites', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_favorites_task_id', 'favorites', ['task_id'], unique=False, postgresql_include=[])
    _replace_index('idx_favorites_user_id', 'favorites', ['user_id'], unique=False, postgresql_include=[])
    _replace_index('idx_favorites_user_story_id', 'favorites', ['user_story_id'], unique=False, postgresql_include=[])
    op.alter_column('labels', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('labels', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('labels', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('labels', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_labels_deleted_at'), table_name='labels', if_exists=True)
    op.drop_index(op.f('ix_labels_project_id'), table_name='labels', if_exists=True)
    _drop_constraint(op.f('idx_project_label_name'), 'labels', type_='unique')
    _replace_index('idx_project_label_name', 'labels', ['project_id', 'name'], unique=True, postgresql_include=[])
    _replace_index('idx_labels_deleted_at', 'labels', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_labels_project_id', 'labels', ['project_id'], unique=False, postgresql_include=[])
    op.alter_column('organization_invitations', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('organization_invitations', 'organization_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"organization_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('organization_invitations', 'role_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"role_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('organization_invitations', 'created_by',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.TEXT(),
               existing_nullable=False,
               autoincrement=False)
    op.drop_index(op.f('ix_organization_invitations_deleted_at'), table_name='organization_invitations', if_exists=True)
    op.drop_index(op.f('ix_organization_invitations_email'), table_name='organization_invitations', if_exists=True)
    op.drop_index(op.f('ix_organization_invitations_organization_id'), table_name='organization_invitations', if_exists=True)
    op.drop_index(op.f('ix_organization_invitations_token'), table_name='organization_invitations', if_exists=True)
    _replace_index('idx_org_invites_deleted_at', 'organization_invitations', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_org_invites_email', 'organization_invitations', ['email'], unique=False, postgresql_include=[])
    _replace_index('idx_org_invites_org_id', 'organization_invitations', ['organization_id'], unique=False, postgresql_include=[])
    _replace_index('idx_org_invites_token', 'organization_invitations', ['token'], unique=False, postgresql_include=[])
    op.create_unique_constraint('uni_organization_invitations_token', 'organization_invitations', ['token'], postgresql_include=[], postgresql_nulls_not_distinct=False)
    op.alter_column('organizations', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('organizations', 'created_by',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.TEXT(),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('organizations', 'is_active',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('true'),
               nullable=True,
               autoincrement=False)
    op.alter_column('organizations', 'team_size',
               existing_type=sa.VARCHAR(),
               type_=sa.TEXT(),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('organizations', 'country',
               existing_type=sa.VARCHAR(),
               type_=sa.TEXT(),
               existing_nullable=False,
               autoincrement=False)
    op.drop_index(op.f('ix_organizations_deleted_at'), table_name='organizations', if_exists=True)
    op.drop_index(op.f('ix_organizations_name'), table_name='organizations', if_exists=True)
    op.drop_index(op.f('ix_organizations_slug'), table_name='organizations', if_exists=True)
    _replace_index('idx_organization_deleted_at', 'organizations', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_organization_name', 'organizations', ['name'], unique=False, postgresql_include=[])
    _replace_index('idx_organization_slug', 'organizations', ['slug'], unique=True, postgresql_include=[])
    op.create_unique_constraint('uni_organizations_name', 'organizations', ['name'], postgresql_include=[], postgresql_nulls_not_distinct=False)
    op.create_unique_constraint('uni_organizations_slug', 'organizations', ['slug'], postgresql_include=[], postgresql_nulls_not_distinct=False)
    op.alter_column('orphaned_files', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('orphaned_files', 'attempts',
               existing_type=sa.INTEGER(),
               server_default=sa.text('0'),
               existing_nullable=False,
               autoincrement=False)
    # Some legacy databases already replaced the two single-column indexes
    # with the composite index. Rebuild it to enforce the same definition for
    # both histories, rather than accepting an existing name without checking it.
    op.drop_index(op.f('ix_orphaned_files_available_at'), table_name='orphaned_files', if_exists=True)
    op.drop_index(op.f('ix_orphaned_files_created_at'), table_name='orphaned_files', if_exists=True)
    op.drop_index(op.f('idx_orphaned_files_available'), table_name='orphaned_files', if_exists=True)
    _replace_index('idx_orphaned_files_available', 'orphaned_files', ['available_at', 'created_at'], unique=False, postgresql_include=[])
    op.alter_column('permissions', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.add_column('project_members', sa.Column('project_role', sa.VARCHAR(length=50), autoincrement=False, nullable=True))
    op.alter_column('project_members', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('project_members', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('project_members', 'user_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('project_members', 'added_by_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"added_by_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('project_members', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('project_members', 'role_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"role_id"::uuid',
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_project_members_deleted_at'), table_name='project_members', if_exists=True)
    op.drop_index(op.f('ix_project_members_role_id'), table_name='project_members', if_exists=True)
    _replace_index('idx_project_members_deleted_at', 'project_members', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_project_members_role_id', 'project_members', ['role_id'], unique=False, postgresql_include=[])
    op.alter_column('projects', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('projects', 'organization_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"organization_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('projects', 'status',
               existing_type=sa.VARCHAR(length=20),
               server_default=sa.text("'active'::character varying"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('projects', 'created_by',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"created_by"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('projects', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('projects', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_projects_created_by'), table_name='projects', if_exists=True)
    op.drop_index(op.f('ix_projects_deleted_at'), table_name='projects', if_exists=True)
    _replace_index('idx_projects_created_by', 'projects', ['created_by'], unique=False, postgresql_include=[])
    _replace_index('idx_projects_deleted_at', 'projects', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_projects_fts', 'projects', [sa.literal_column("to_tsvector('english'::regconfig, (((COALESCE(name, ''::character varying)::text || ' '::text) || COALESCE(description, ''::text)) || ' '::text) || COALESCE(slug, ''::character varying)::text)")], unique=False, postgresql_using='gin', postgresql_include=[])
    _replace_index('idx_projects_slug', 'projects', ['slug'], unique=True, postgresql_where='(deleted_at IS NULL)', postgresql_include=[])
    op.alter_column('refresh_tokens', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('refresh_tokens', 'user_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.drop_index(op.f('ix_refresh_tokens_deleted_at'), table_name='refresh_tokens', if_exists=True)
    op.drop_index(op.f('ix_refresh_tokens_user_id'), table_name='refresh_tokens', if_exists=True)
    _drop_constraint(op.f('refresh_tokens_token_hash_key'), 'refresh_tokens', type_='unique')
    _replace_index('idx_refresh_tokens_deleted_at', 'refresh_tokens', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_refresh_tokens_user_id', 'refresh_tokens', ['user_id'], unique=False, postgresql_include=[])
    op.create_unique_constraint('uni_refresh_tokens_token_hash', 'refresh_tokens', ['token_hash'], postgresql_include=[], postgresql_nulls_not_distinct=False)
    op.create_unique_constraint('uni_refresh_tokens_user_id', 'refresh_tokens', ['user_id'], postgresql_include=[], postgresql_nulls_not_distinct=False)
    op.alter_column('role_permissions', 'role_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"role_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('role_permissions', 'permission_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"permission_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('roles', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('roles', 'organization_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"organization_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('roles', 'is_system',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('roles', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_roles_deleted_at'), table_name='roles', if_exists=True)
    op.drop_index(op.f('ix_roles_organization_id'), table_name='roles', if_exists=True)
    _replace_index('idx_roles_deleted_at', 'roles', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_roles_organization_id', 'roles', ['organization_id'], unique=False, postgresql_include=[])
    op.alter_column('sprint_snapshots', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('sprint_snapshots', 'sprint_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"sprint_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('sprint_snapshots', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_sprint_snapshots_sprint_id'), table_name='sprint_snapshots', if_exists=True)
    _drop_constraint(op.f('idx_sprint_snapshot_sprint_date'), 'sprint_snapshots', type_='unique')
    _replace_index('idx_sprint_snapshot_sprint_date', 'sprint_snapshots', ['sprint_id', 'date'], unique=True, postgresql_include=[])
    _replace_index('idx_sprint_snapshots_sprint_id', 'sprint_snapshots', ['sprint_id'], unique=False, postgresql_include=[])
    op.alter_column('sprints', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('sprints', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('sprints', 'status',
               existing_type=sa.VARCHAR(length=20),
               server_default=sa.text("'planned'::character varying"),
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('sprints', 'created_by_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"created_by_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('sprints', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('sprints', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_sprints_deleted_at'), table_name='sprints', if_exists=True)
    op.drop_index(op.f('ix_sprints_project_id'), table_name='sprints', if_exists=True)
    _replace_index('idx_sprints_deleted_at', 'sprints', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_sprints_fts', 'sprints', [sa.literal_column("to_tsvector('english'::regconfig, (COALESCE(name, ''::character varying)::text || ' '::text) || COALESCE(goal, ''::character varying)::text)")], unique=False, postgresql_using='gin', postgresql_include=[])
    _replace_index('idx_sprints_project_id', 'sprints', ['project_id'], unique=False, postgresql_include=[])
    op.alter_column('task_attachments', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('task_attachments', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               nullable=True,
               autoincrement=False)
    op.alter_column('task_attachments', 'task_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"task_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('task_attachments', 'url',
               existing_type=sa.TEXT(),
               server_default=sa.text("'''::text'::text"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('task_attachments', 'uploaded_by',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"uploaded_by"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.drop_index(op.f('ix_task_attachments_project_id'), table_name='task_attachments', if_exists=True)
    op.drop_index(op.f('ix_task_attachments_task_id'), table_name='task_attachments', if_exists=True)
    op.drop_index(op.f('ix_task_attachments_uploaded_by'), table_name='task_attachments', if_exists=True)
    _replace_index('idx_task_attachments_project_id', 'task_attachments', ['project_id'], unique=False, postgresql_include=[])
    _replace_index('idx_task_attachments_task_id', 'task_attachments', ['task_id'], unique=False, postgresql_include=[])
    _replace_index('idx_task_attachments_uploaded_by', 'task_attachments', ['uploaded_by'], unique=False, postgresql_include=[])
    op.alter_column('task_labels', 'task_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"task_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('task_labels', 'label_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"label_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'sprint_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"sprint_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('tasks', 'user_story_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_story_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('tasks', 'serial_number',
               existing_type=sa.BIGINT(),
               nullable=True,
               autoincrement=False)
    op.alter_column('tasks', 'type',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'task'::character varying"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'priority',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'medium'::character varying"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'status_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"status_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'status',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'todo'::character varying"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'assignee_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"assignee_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('tasks', 'reporter_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"reporter_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('tasks', 'story_points',
               existing_type=sa.INTEGER(),
               server_default=sa.text('0'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('tasks', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('tasks', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_tasks_assignee_id'), table_name='tasks', if_exists=True)
    op.drop_index(op.f('ix_tasks_deleted_at'), table_name='tasks', if_exists=True)
    op.drop_index(op.f('ix_tasks_project_id'), table_name='tasks', if_exists=True)
    op.drop_index(op.f('ix_tasks_reporter_id'), table_name='tasks', if_exists=True)
    op.drop_index(op.f('ix_tasks_sequence_number'), table_name='tasks', if_exists=True)
    op.drop_index(op.f('ix_tasks_sprint_id'), table_name='tasks', if_exists=True)
    op.drop_index(op.f('ix_tasks_status_id'), table_name='tasks', if_exists=True)
    op.drop_index(op.f('ix_tasks_user_story_id'), table_name='tasks', if_exists=True)
    _drop_constraint(op.f('tasks_serial_number_key'), 'tasks', type_='unique')
    _drop_constraint(op.f('idx_project_task_key'), 'tasks', type_='unique')
    _replace_index('idx_project_task_key', 'tasks', ['project_id', 'key'], unique=True, postgresql_include=[])
    _replace_index('idx_tasks_assignee_id', 'tasks', ['assignee_id'], unique=False, postgresql_include=[])
    _replace_index('idx_tasks_deleted_at', 'tasks', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_tasks_fts', 'tasks', [sa.literal_column("to_tsvector('english'::regconfig, (((COALESCE(title, ''::character varying)::text || ' '::text) || COALESCE(description, ''::text)) || ' '::text) || COALESCE(key, ''::character varying)::text)")], unique=False, postgresql_using='gin', postgresql_include=[])
    _replace_index('idx_tasks_project_id', 'tasks', ['project_id'], unique=False, postgresql_include=[])
    _replace_index('idx_tasks_reporter_id', 'tasks', ['reporter_id'], unique=False, postgresql_include=[])
    _replace_index('idx_tasks_sequence_number', 'tasks', ['sequence_number'], unique=False, postgresql_include=[])
    _replace_index('idx_tasks_serial_number', 'tasks', ['serial_number'], unique=True, postgresql_include=[])
    _replace_index('idx_tasks_sprint_id', 'tasks', ['sprint_id'], unique=False, postgresql_include=[])
    _replace_index('idx_tasks_status_id', 'tasks', ['status_id'], unique=False, postgresql_include=[])
    _replace_index('idx_tasks_user_story_id', 'tasks', ['user_story_id'], unique=False, postgresql_include=[])
    op.add_column('user_stories', sa.Column('status', sa.TEXT(), autoincrement=False, nullable=True))
    op.alter_column('user_stories', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'sprint_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"sprint_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('user_stories', 'serial_number',
               existing_type=sa.BIGINT(),
               nullable=True,
               autoincrement=False)
    op.alter_column('user_stories', 'priority',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'medium'::character varying"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'status_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"status_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'story_points',
               existing_type=sa.INTEGER(),
               server_default=sa.text('0'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'backlog_order',
               existing_type=sa.INTEGER(),
               server_default=sa.text('0'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'assignee_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"assignee_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('user_stories', 'reporter_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"reporter_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('user_stories', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('user_stories', 'is_closed',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_stories', 'key',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'''::character varying'::character varying"),
               nullable=True,
               autoincrement=False)
    op.alter_column('user_stories', 'sequence_number',
               existing_type=sa.INTEGER(),
               nullable=True,
               autoincrement=False,
               existing_server_default=sa.text('0'))
    op.drop_index(op.f('ix_user_stories_assignee_id'), table_name='user_stories', if_exists=True)
    op.drop_index(op.f('ix_user_stories_deleted_at'), table_name='user_stories', if_exists=True)
    op.drop_index(op.f('ix_user_stories_project_id'), table_name='user_stories', if_exists=True)
    op.drop_index(op.f('ix_user_stories_reporter_id'), table_name='user_stories', if_exists=True)
    op.drop_index(op.f('ix_user_stories_sequence_number'), table_name='user_stories', if_exists=True)
    op.drop_index(op.f('ix_user_stories_sprint_id'), table_name='user_stories', if_exists=True)
    op.drop_index(op.f('ix_user_stories_status_id'), table_name='user_stories', if_exists=True)
    _drop_constraint(op.f('user_stories_serial_number_key'), 'user_stories', type_='unique')
    _replace_index('idx_user_stories_assignee_id', 'user_stories', ['assignee_id'], unique=False, postgresql_include=[])
    _replace_index('idx_user_stories_deleted_at', 'user_stories', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_user_stories_fts', 'user_stories', [sa.literal_column("to_tsvector('english'::regconfig, (((COALESCE(title, ''::character varying)::text || ' '::text) || COALESCE(description, ''::text)) || ' '::text) || COALESCE(key, ''::character varying)::text)")], unique=False, postgresql_using='gin', postgresql_include=[])
    _replace_index('idx_user_stories_project_id', 'user_stories', ['project_id'], unique=False, postgresql_include=[])
    _replace_index('idx_user_stories_reporter_id', 'user_stories', ['reporter_id'], unique=False, postgresql_include=[])
    _replace_index('idx_user_stories_sequence_number', 'user_stories', ['sequence_number'], unique=False, postgresql_include=[])
    _replace_index('idx_user_stories_serial_number', 'user_stories', ['serial_number'], unique=True, postgresql_include=[])
    _replace_index('idx_user_stories_sprint_id', 'user_stories', ['sprint_id'], unique=False, postgresql_include=[])
    _replace_index('idx_user_stories_status_id', 'user_stories', ['status_id'], unique=False, postgresql_include=[])
    op.alter_column('user_story_attachments', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_story_attachments', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               nullable=True,
               autoincrement=False)
    op.alter_column('user_story_attachments', 'user_story_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"user_story_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('user_story_attachments', 'url',
               existing_type=sa.TEXT(),
               server_default=sa.text("'''::text'::text"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_story_attachments', 'uploaded_by',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"uploaded_by"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.drop_index(op.f('ix_user_story_attachments_project_id'), table_name='user_story_attachments', if_exists=True)
    op.drop_index(op.f('ix_user_story_attachments_uploaded_by'), table_name='user_story_attachments', if_exists=True)
    op.drop_index(op.f('ix_user_story_attachments_user_story_id'), table_name='user_story_attachments', if_exists=True)
    _replace_index('idx_user_story_attachments_project_id', 'user_story_attachments', ['project_id'], unique=False, postgresql_include=[])
    _replace_index('idx_user_story_attachments_uploaded_by', 'user_story_attachments', ['uploaded_by'], unique=False, postgresql_include=[])
    _replace_index('idx_user_story_attachments_user_story_id', 'user_story_attachments', ['user_story_id'], unique=False, postgresql_include=[])
    op.alter_column('user_story_statuses', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_story_statuses', 'project_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"project_id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_story_statuses', 'is_default',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_story_statuses', 'is_closed',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_story_statuses', 'is_final',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('user_story_statuses', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.alter_column('user_story_statuses', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               autoincrement=False)
    op.drop_index(op.f('ix_user_story_statuses_deleted_at'), table_name='user_story_statuses', if_exists=True)
    op.drop_index(op.f('ix_user_story_statuses_project_id'), table_name='user_story_statuses', if_exists=True)
    _replace_index('idx_user_story_statuses_deleted_at', 'user_story_statuses', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_user_story_statuses_project_id', 'user_story_statuses', ['project_id'], unique=False, postgresql_include=[])
    op.alter_column('users', 'id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"id"::uuid',
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('users', 'organization_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"organization_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('users', 'password_hash',
               existing_type=sa.VARCHAR(length=255),
               type_=sa.TEXT(),
               nullable=True,
               autoincrement=False)
    op.alter_column('users', 'role_id',
               existing_type=sa.VARCHAR(length=36),
               type_=sa.UUID(),
               postgresql_using='"role_id"::uuid',
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('users', 'color',
               existing_type=sa.VARCHAR(length=7),
               server_default=sa.text("'#3498DB'::character varying"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('users', 'timezone',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'UTC'::character varying"),
               existing_nullable=True,
               autoincrement=False)
    op.alter_column('users', 'is_active',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('true'),
               nullable=True,
               autoincrement=False)
    op.alter_column('users', 'is_verified',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               nullable=True,
               autoincrement=False)
    op.alter_column('users', 'status',
               existing_type=sa.VARCHAR(length=50),
               server_default=sa.text("'active'::character varying"),
               existing_nullable=False,
               autoincrement=False)
    op.alter_column('users', 'require_password_change',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False,
               autoincrement=False)
    op.drop_index(op.f('ix_users_deleted_at'), table_name='users', if_exists=True)
    op.drop_index(op.f('ix_users_email'), table_name='users', if_exists=True)
    op.drop_index(op.f('ix_users_organization_id'), table_name='users', if_exists=True)
    op.drop_index(op.f('ix_users_role_id'), table_name='users', if_exists=True)
    op.drop_index(op.f('ix_users_username'), table_name='users', if_exists=True)
    _replace_index('idx_users_deleted_at', 'users', ['deleted_at'], unique=False, postgresql_include=[])
    _replace_index('idx_users_email', 'users', ['email'], unique=False, postgresql_include=[])
    _replace_index('idx_users_fts', 'users', [sa.literal_column("to_tsvector('english'::regconfig, (((COALESCE(full_name, ''::character varying)::text || ' '::text) || COALESCE(email, ''::character varying)::text) || ' '::text) || COALESCE(username, ''::character varying)::text)")], unique=False, postgresql_using='gin', postgresql_include=[])
    _replace_index('idx_users_organization_id', 'users', ['organization_id'], unique=False, postgresql_include=[])
    _replace_index('idx_users_role_id', 'users', ['role_id'], unique=False, postgresql_include=[])
    _replace_index('idx_users_username', 'users', ['username'], unique=False, postgresql_include=[])
    op.create_unique_constraint('uni_users_email', 'users', ['email'], postgresql_include=[], postgresql_nulls_not_distinct=False)
    op.create_unique_constraint('uni_users_username', 'users', ['username'], postgresql_include=[], postgresql_nulls_not_distinct=False)
    op.create_foreign_key('fk_organization_invitations_role', 'organization_invitations', 'roles', ['role_id'], ['id'])
    op.create_foreign_key('fk_organization_invitations_organization', 'organization_invitations', 'organizations', ['organization_id'], ['id'])
    op.create_foreign_key('fk_role_permissions_permission', 'role_permissions', 'permissions', ['permission_id'], ['id'])
    op.create_foreign_key('fk_role_permissions_role', 'role_permissions', 'roles', ['role_id'], ['id'])
    op.create_foreign_key('fk_users_organization', 'users', 'organizations', ['organization_id'], ['id'])
    op.create_foreign_key('fk_users_role', 'users', 'roles', ['role_id'], ['id'])
    op.create_foreign_key('fk_projects_creator', 'projects', 'users', ['created_by'], ['id'])
    op.create_foreign_key('fk_projects_organization', 'projects', 'organizations', ['organization_id'], ['id'])
    op.create_foreign_key('fk_comments_project', 'comments', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_comments_user', 'comments', 'users', ['user_id'], ['id'])
    op.create_foreign_key('fk_comments_parent_comment', 'comments', 'comments', ['parent_comment_id'], ['id'])
    op.create_foreign_key('fk_custom_statuses_project', 'custom_statuses', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_labels_project', 'labels', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_project_members_user', 'project_members', 'users', ['user_id'], ['id'])
    op.create_foreign_key('fk_project_members_added_by', 'project_members', 'users', ['added_by_id'], ['id'])
    op.create_foreign_key('fk_project_members_role', 'project_members', 'roles', ['role_id'], ['id'])
    op.create_foreign_key('fk_project_members_project', 'project_members', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_sprints_created_by', 'sprints', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_sprints_project', 'sprints', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('task_attachments_project_id_fkey', 'task_attachments', 'projects', ['project_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('user_story_attachments_project_id_fkey', 'user_story_attachments', 'projects', ['project_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_user_story_statuses_project', 'user_story_statuses', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_comments_attachments', 'comment_attachments', 'comments', ['comment_id'], ['id'])
    op.create_foreign_key('fk_sprint_snapshots_sprint', 'sprint_snapshots', 'sprints', ['sprint_id'], ['id'])
    op.create_foreign_key('fk_user_stories_sprint', 'user_stories', 'sprints', ['sprint_id'], ['id'])
    op.create_foreign_key('fk_user_stories_assignee', 'user_stories', 'users', ['assignee_id'], ['id'])
    op.create_foreign_key('fk_user_stories_reporter', 'user_stories', 'users', ['reporter_id'], ['id'])
    op.create_foreign_key('fk_user_stories_project', 'user_stories', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_tasks_project', 'tasks', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_tasks_user_story', 'tasks', 'user_stories', ['user_story_id'], ['id'])
    op.create_foreign_key('fk_tasks_assignee', 'tasks', 'users', ['assignee_id'], ['id'])
    op.create_foreign_key('fk_tasks_reporter', 'tasks', 'users', ['reporter_id'], ['id'])
    op.create_foreign_key('fk_tasks_sprint', 'tasks', 'sprints', ['sprint_id'], ['id'])
    op.create_foreign_key('fk_audit_logs_project', 'audit_logs', 'projects', ['project_id'], ['id'])
    op.create_foreign_key('fk_audit_logs_user', 'audit_logs', 'users', ['user_id'], ['id'])
    op.create_foreign_key('fk_audit_logs_sprint', 'audit_logs', 'sprints', ['sprint_id'], ['id'])
    op.create_foreign_key('fk_audit_logs_user_story', 'audit_logs', 'user_stories', ['user_story_id'], ['id'])
    op.create_foreign_key('fk_audit_logs_task', 'audit_logs', 'tasks', ['task_id'], ['id'])
    op.create_foreign_key('fk_favorites_task', 'favorites', 'tasks', ['task_id'], ['id'])
    op.create_foreign_key('fk_favorites_user_story', 'favorites', 'user_stories', ['user_story_id'], ['id'])
    op.create_foreign_key('fk_favorites_user', 'favorites', 'users', ['user_id'], ['id'])
    op.create_foreign_key('fk_task_labels_label', 'task_labels', 'labels', ['label_id'], ['id'])
    op.create_foreign_key('fk_task_labels_task', 'task_labels', 'tasks', ['task_id'], ['id'])


def downgrade():
    raise RuntimeError("This schema consolidation is forward-only; restore a verified backup to revert.")
