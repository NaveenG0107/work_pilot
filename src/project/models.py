from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, Text, DateTime, Integer, Index, UniqueConstraint, event, text, literal
from sqlalchemy.sql import sqltypes
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("idx_projects_created_by", "created_by"),
        Index("idx_projects_deleted_at", "deleted_at"),
        Index("idx_projects_slug", "slug", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("idx_projects_fts", text("to_tsvector('english', coalesce(name, '') || ' ' || coalesce(description, '') || ' ' || coalesce(slug, ''))"), postgresql_using="gin"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(UUID(as_uuid=False), ForeignKey("organizations.id", name="fk_projects_organization"), nullable=False)
    name = Column(String(150), nullable=False)
    slug = Column(String(150), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="active", server_default="active")
    created_by = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_projects_creator"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", foreign_keys=[organization_id])
    creator = relationship("User", foreign_keys=[created_by])

    sprint_count = None
    sprints = None


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        Index("idx_project_members_deleted_at", "deleted_at"),
        Index("idx_project_members_role_id", "role_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_role = Column(String(50), nullable=True)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_project_members_project"), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_project_members_user"), nullable=False)
    joined_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    added_by_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_project_members_added_by"), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    role_id = Column(UUID(as_uuid=False), ForeignKey("roles.id", name="fk_project_members_role"), nullable=True)

    role = relationship("Role", foreign_keys=[role_id])
    project = relationship("Project", foreign_keys=[project_id])
    user = relationship("User", foreign_keys=[user_id])
    added_by = relationship("User", foreign_keys=[added_by_id])


@event.listens_for(Project, "before_insert")
def project_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())


@event.listens_for(ProjectMember, "before_insert")
def project_member_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())
