from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, Text, DateTime, Integer, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(String(36), ForeignKey("organizations.id"), nullable=False)
    name = Column(String(150), nullable=False)
    slug = Column(String(150), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="active")
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    organization = relationship("Organization", foreign_keys=[organization_id])
    creator = relationship("User", foreign_keys=[created_by])

    sprint_count = None
    sprints = None


class ProjectMember(Base):
    __tablename__ = "project_members"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime(timezone=True), nullable=False)
    added_by_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

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