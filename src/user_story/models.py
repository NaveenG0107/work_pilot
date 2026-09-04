from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, event, text
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


def format_serial_number(seq: int) -> str:
    if seq <= 0:
        return ""

    return f"US-{seq}"


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

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    sprint_id = Column(String(36), ForeignKey("sprints.id"), nullable=True, index=True)
    key = Column(String(50), nullable=True)
    sequence_number = Column(Integer, nullable=True, index=True)
    serial_number = Column(BigInteger, nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(50), nullable=False, default="medium")
    status_id = Column(String(36), ForeignKey("user_story_statuses.id"), nullable=False, index=True)
    is_closed = Column(Boolean, nullable=False, default=False)
    story_points = Column(Integer, nullable=False, default=0)
    backlog_order = Column(Integer, nullable=False, default=0)
    assignee_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    reporter_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    project = relationship("Project", foreign_keys=[project_id])
    sprint = relationship("Sprint", foreign_keys=[sprint_id])
    status = relationship("UserStoryStatus", foreign_keys=[status_id])
    assignee = relationship("User", foreign_keys=[assignee_id])
    reporter = relationship("User", foreign_keys=[reporter_id])
    attachments = relationship("UserStoryAttachment", back_populates="user_story", foreign_keys="UserStoryAttachment.user_story_id")

    __table_args__ = (
        Index(
            "idx_project_user_story_key",
            "project_id",
            "key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    @property
    def formatted_serial_number(self):
        if self.key:
            return self.key
        if self.sequence_number and self.sequence_number > 0:
            return f"US-{self.sequence_number}"
        return format_serial_number(self.serial_number)


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

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_story_id = Column(String(36), ForeignKey("user_stories.id"), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    storage_path = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default="")
    uploaded_by = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    user_story = relationship("UserStory", back_populates="attachments", foreign_keys=[user_story_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])


@event.listens_for(UserStory, "before_insert")
def user_story_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.serial_number:
        target.serial_number = get_next_global_serial_number(connection)


@event.listens_for(UserStoryAttachment, "before_insert")
def user_story_attachment_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.uploaded_at:
        target.uploaded_at = datetime.now(timezone.utc)
