from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Integer, String, Table, Text, UniqueConstraint, event, text
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


task_labels = Table(
    "task_labels",
    Base.metadata,
    Column("task_id", String(36), ForeignKey("tasks.id"), primary_key=True),
    Column("label_id", String(36), ForeignKey("labels.id"), primary_key=True),
)


class TaskAttachment(Base):
    __tablename__ = "task_attachments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    storage_path = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default="")
    uploaded_by = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    task = relationship("Task", back_populates="attachments", foreign_keys=[task_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    sprint_id = Column(String(36), ForeignKey("sprints.id"), nullable=True, index=True)
    user_story_id = Column(String(36), ForeignKey("user_stories.id"), nullable=True, index=True)
    key = Column(String(50), nullable=False)
    sequence_number = Column(Integer, nullable=False, index=True)
    serial_number = Column(BigInteger, nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    type = Column(String(50), nullable=False, default="task")
    priority = Column(String(50), nullable=False, default="medium")
    status_id = Column(String(36), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="todo")
    blocked_reason = Column(Text, nullable=True)
    assignee_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    reporter_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    story_points = Column(Integer, nullable=False, default=0)
    due_date = Column(DateTime(timezone=True), nullable=True)
    estimated_hours = Column(Float, nullable=True)
    actual_hours = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    project = relationship("Project", foreign_keys=[project_id])
    sprint = relationship("Sprint", foreign_keys=[sprint_id])
    user_story = relationship("UserStory", foreign_keys=[user_story_id])
    assignee = relationship("User", foreign_keys=[assignee_id])
    reporter = relationship("User", foreign_keys=[reporter_id])
    labels = relationship("Label", secondary=task_labels)
    attachments = relationship("TaskAttachment", back_populates="task", foreign_keys=[TaskAttachment.task_id])

    __table_args__ = (
        UniqueConstraint("project_id", "key", name="idx_project_task_key"),
    )

    @property
    def formatted_serial_number(self):
        return format_serial_number(self.serial_number)


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


def format_serial_number(seq: int) -> str:
    if seq <= 0:
        return ""

    return f"#{seq}"


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

    if not target.serial_number:
        target.serial_number = get_next_global_serial_number(connection)
