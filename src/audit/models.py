import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, Index, String, Text, DateTime, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, foreign

from uuid6 import uuid7

from src.database import Base


class AuditLogType:
    VIEW = "view"
    ACTIVITY = "activity"
    AUDIT = "audit"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_user_id", "user_id"),
        Index("idx_audit_logs_org_id", "organization_id"),
        Index("idx_audit_logs_action", "action"),
        Index("idx_audit_logs_resource_type", "resource_type"),
        Index("idx_audit_logs_project_id", "project_id"),
        Index("idx_audit_logs_task_id", "task_id"),
        Index("idx_audit_logs_sprint_id", "sprint_id"),
        Index("idx_audit_logs_user_story_id", "user_story_id"),
        Index("idx_audit_logs_type", "type"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_audit_logs_user"), nullable=True)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_audit_logs_project"), nullable=True)
    task_id = Column(UUID(as_uuid=False), ForeignKey("tasks.id", name="fk_audit_logs_task"), nullable=True)
    sprint_id = Column(UUID(as_uuid=False), ForeignKey("sprints.id", name="fk_audit_logs_sprint"), nullable=True)
    user_story_id = Column(UUID(as_uuid=False), ForeignKey("user_stories.id", name="fk_audit_logs_user_story"), nullable=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(255), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    type = Column(String(50), nullable=True, default=AuditLogType.ACTIVITY, server_default="activity")

    organization = relationship("Organization", primaryjoin="foreign(AuditLog.organization_id) == Organization.id", foreign_keys=[organization_id])
    user = relationship("User", foreign_keys=[user_id])
    project = relationship("Project", foreign_keys=[project_id])
    task = relationship("Task", foreign_keys=[task_id])
    sprint = relationship("Sprint", foreign_keys=[sprint_id])
    user_story = relationship("UserStory", foreign_keys=[user_story_id])


@event.listens_for(AuditLog, "before_insert")
def audit_log_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())
