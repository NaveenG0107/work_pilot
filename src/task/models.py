from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Numeric, ForeignKey, Integer, String, Table, Text, Index, event, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, synonym, foreign

from uuid6 import uuid7

from src.database import Base
from src.serial.models import format_serial_number


task_labels = Table(
    "task_labels",
    Base.metadata,
    Column("task_id", UUID(as_uuid=False), ForeignKey("tasks.id", name="fk_task_labels_task"), primary_key=True),
    Column("label_id", UUID(as_uuid=False), ForeignKey("labels.id", name="fk_task_labels_label"), primary_key=True),
)


class TaskAttachment(Base):
    __tablename__ = "task_attachments"
    __table_args__ = (
        Index("idx_task_attachments_project_id", "project_id"),
        Index("idx_task_attachments_task_id", "task_id"),
        Index("idx_task_attachments_uploaded_by", "uploaded_by"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", ondelete="CASCADE", name="task_attachments_project_id_fkey"), nullable=True)
    task_id = Column(UUID(as_uuid=False), nullable=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    storage_path = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default="", server_default=text("''::text"))
    uploaded_by = Column(UUID(as_uuid=False), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    task = relationship("Task", back_populates="attachments", foreign_keys=[task_id], primaryjoin="foreign(TaskAttachment.task_id) == Task.id")
    uploader = relationship("User", foreign_keys=[uploaded_by], primaryjoin="foreign(TaskAttachment.uploaded_by) == User.id")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("idx_project_task_key", "project_id", "key", unique=True),
        Index("idx_tasks_assignee_id", "assignee_id"),
        Index("idx_tasks_deleted_at", "deleted_at"),
        Index("idx_tasks_fts", text("to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(key, ''))"), postgresql_using="gin"),
        Index("idx_tasks_project_id", "project_id"),
        Index("idx_tasks_reporter_id", "reporter_id"),
        Index("idx_tasks_sequence_number", "sequence_number"),
        Index("idx_tasks_serial_number", "serial_number", unique=True),
        Index("idx_tasks_sprint_id", "sprint_id"),
        Index("idx_tasks_status_id", "status_id"),
        Index("idx_tasks_user_story_id", "user_story_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_tasks_project"), nullable=False)
    sprint_id = Column(UUID(as_uuid=False), ForeignKey("sprints.id", name="fk_tasks_sprint"), nullable=True)
    user_story_id = Column(UUID(as_uuid=False), ForeignKey("user_stories.id", name="fk_tasks_user_story"), nullable=True)
    key = Column(String(50), nullable=False)
    sequence_number = Column(Integer, nullable=False)
    serial_number = Column(BigInteger, nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    type = Column(String(50), nullable=False, default="task", server_default="task")
    priority = Column(String(50), nullable=False, default="medium", server_default="medium")
    status_id = Column(UUID(as_uuid=False), nullable=False)
    status = Column(String(50), nullable=False, default="todo", server_default="todo")
    assignee_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_tasks_assignee"), nullable=True)
    reporter_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_tasks_reporter"), nullable=True)
    story_points = Column(Integer, nullable=False, default=0, server_default="0")
    due_date = Column(DateTime(timezone=True), nullable=True)
    estimated_hours = Column(Numeric, nullable=True)
    actual_hours = Column(Numeric, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", foreign_keys=[project_id])
    sprint = relationship("Sprint", foreign_keys=[sprint_id])
    user_story = relationship("UserStory", foreign_keys=[user_story_id])
    assignee = relationship("User", foreign_keys=[assignee_id])
    reporter = relationship("User", foreign_keys=[reporter_id])
    status_rel = relationship("CustomStatus", foreign_keys=[status_id], primaryjoin="foreign(Task.status_id) == CustomStatus.id")
    labels = relationship("Label", secondary=task_labels)
    attachments = relationship("TaskAttachment", back_populates="task", foreign_keys="TaskAttachment.task_id", primaryjoin="Task.id == foreign(TaskAttachment.task_id)")

    @property
    def formatted_serial_number(self):
        return format_serial_number(self.serial_number or self.sequence_number)


class TaskAccessContext:
    def __init__(self, task_id, project_id, organization_id, task_key):
        self.task_id = task_id
        self.project_id = project_id
        self.organization_id = organization_id
        self.task_key = task_key


DEFAULT_STATUS_COLORS = {
    "todo": "#808080",
    "in_progress": "#1E90FF",
    "in_review": "#FF8C00",
    "testing": "#8A2BE2",
    "completed": "#228B22",
    "blocked": "#DC143C",
}


DEFAULT_STATUS_IS_FINAL = {
    "todo": False,
    "in_progress": False,
    "in_review": False,
    "testing": False,
    "completed": True,
    "blocked": False,
}


def normalize_task_status(status: str) -> str:
    status = status.lower().strip()
    return status.replace(" ", "_")


def is_default_task_status(status: str) -> bool:
    return normalize_task_status(status) in DEFAULT_STATUS_COLORS


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


@event.listens_for(TaskAttachment, "before_insert")
def task_attachment_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.uploaded_at:
        target.uploaded_at = datetime.now(timezone.utc)


@event.listens_for(Task, "before_insert")
def task_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.sequence_number:
        target.sequence_number = get_next_global_serial_number(connection)

    if not target.serial_number:
        target.serial_number = target.sequence_number
