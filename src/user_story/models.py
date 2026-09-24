from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, event, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, synonym, foreign

from uuid6 import uuid7

from src.database import Base
from src.serial.models import format_serial_number


def get_next_global_serial_number(connection) -> int:
    try:
        nested = connection.begin_nested()
        result = connection.execute(
            text("SELECT nextval('global_work_item_serial_seq')")
        )
        next_value = result.scalar()
        nested.commit()
        if next_value and next_value > 0:
            return next_value
    except Exception:
        try:
            nested.rollback()
        except Exception:
            pass

    max_task = connection.execute(
        text("SELECT COALESCE(MAX(serial_number), 0) FROM tasks")
    ).scalar() or 0

    max_story = connection.execute(
        text("SELECT COALESCE(MAX(serial_number), 0) FROM user_stories")
    ).scalar() or 0

    return max(max_task, max_story) + 1


class UserStory(Base):
    __tablename__ = "user_stories"
    __table_args__ = (
        Index("idx_project_user_story_key", "project_id", "key", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("idx_user_stories_assignee_id", "assignee_id"),
        Index("idx_user_stories_deleted_at", "deleted_at"),
        Index("idx_user_stories_fts", text("to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(key, ''))"), postgresql_using="gin"),
        Index("idx_user_stories_project_id", "project_id"),
        Index("idx_user_stories_reporter_id", "reporter_id"),
        Index("idx_user_stories_sequence_number", "sequence_number"),
        Index("idx_user_stories_serial_number", "serial_number", unique=True),
        Index("idx_user_stories_sprint_id", "sprint_id"),
        Index("idx_user_stories_status_id", "status_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_user_stories_project"), nullable=False)
    sprint_id = Column(UUID(as_uuid=False), ForeignKey("sprints.id", name="fk_user_stories_sprint"), nullable=True)
    serial_number = Column(BigInteger, nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(50), nullable=False, default="medium", server_default="medium")
    status_id = Column(UUID(as_uuid=False), nullable=False)
    status_text = Column("status", Text, nullable=True)
    story_points = Column(Integer, nullable=False, default=0, server_default="0")
    backlog_order = Column(Integer, nullable=False, default=0, server_default="0")
    assignee_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_user_stories_assignee"), nullable=True)
    reporter_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_user_stories_reporter"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    is_closed = Column(Boolean, nullable=False, default=False, server_default="false")
    key = Column(String(50), nullable=True, default="", server_default=text("''::character varying"))
    sequence_number = Column(Integer, nullable=True, default=0, server_default="0")

    project = relationship("Project", foreign_keys=[project_id])
    sprint = relationship("Sprint", foreign_keys=[sprint_id])
    status = relationship("UserStoryStatus", foreign_keys=[status_id], primaryjoin="foreign(UserStory.status_id) == UserStoryStatus.id")
    assignee = relationship("User", foreign_keys=[assignee_id])
    reporter = relationship("User", foreign_keys=[reporter_id])
    attachments = relationship("UserStoryAttachment", back_populates="user_story", foreign_keys="UserStoryAttachment.user_story_id", primaryjoin="UserStory.id == foreign(UserStoryAttachment.user_story_id)")

    @property
    def formatted_serial_number(self):
        return format_serial_number(self.serial_number or self.sequence_number)


class StoryTaskStats:
    def __init__(self, user_story_id, total_tasks, completed):
        self.user_story_id = user_story_id
        self.total_tasks = total_tasks
        self.completed = completed


class UserStoryAccessContext:
    def __init__(self, user_story_id, project_id, organization_id, title):
        self.user_story_id = user_story_id
        self.project_id = project_id
        self.organization_id = organization_id
        self.title = title


class UserStoryAttachment(Base):
    __tablename__ = "user_story_attachments"
    __table_args__ = (
        Index("idx_user_story_attachments_project_id", "project_id"),
        Index("idx_user_story_attachments_uploaded_by", "uploaded_by"),
        Index("idx_user_story_attachments_user_story_id", "user_story_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", ondelete="CASCADE", name="user_story_attachments_project_id_fkey"), nullable=True)
    user_story_id = Column(UUID(as_uuid=False), nullable=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    storage_path = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default="", server_default=text("''::text"))
    uploaded_by = Column(UUID(as_uuid=False), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    user_story = relationship("UserStory", back_populates="attachments", foreign_keys=[user_story_id], primaryjoin="foreign(UserStoryAttachment.user_story_id) == UserStory.id")
    uploader = relationship("User", foreign_keys=[uploaded_by], primaryjoin="foreign(UserStoryAttachment.uploaded_by) == User.id")


@event.listens_for(UserStory, "before_insert")
def user_story_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.sequence_number:
        target.sequence_number = get_next_global_serial_number(connection)

    if not target.serial_number:
        target.serial_number = target.sequence_number


@event.listens_for(UserStoryAttachment, "before_insert")
def user_story_attachment_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.uploaded_at:
        target.uploaded_at = datetime.now(timezone.utc)
