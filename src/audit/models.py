import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, Text, DateTime, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class AuditLogType:
    VIEW = "view"
    ACTIVITY = "activity"
    AUDIT = "audit"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    organization_id = Column(String(36), nullable=True, index=True)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True, index=True)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=True, index=True)
    sprint_id = Column(String(36), ForeignKey("sprints.id"), nullable=True, index=True)
    user_story_id = Column(String(36), ForeignKey("user_stories.id"), nullable=True, index=True)

    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False, index=True)
    resource_id = Column(String(255), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    type = Column(String(50), default=AuditLogType.ACTIVITY, nullable=False, index=True)

    user = relationship("User", foreign_keys=[user_id])
    project = relationship("Project", foreign_keys=[project_id])
    task = relationship("Task", foreign_keys=[task_id])
    sprint = relationship("Sprint", foreign_keys=[sprint_id])
    user_story = relationship("UserStory", foreign_keys=[user_story_id])


@event.listens_for(AuditLog, "before_insert")
def audit_log_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.type:
        if "view" in target.action.lower():
            target.type = AuditLogType.VIEW
        else:
            target.type = AuditLogType.ACTIVITY