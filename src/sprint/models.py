from datetime import datetime, timezone

from sqlalchemy import (Column, ForeignKey, String, DateTime, Integer, Date, UniqueConstraint, Index, text, event)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from uuid6 import uuid7
from src.database import Base


class Sprint(Base):
    __tablename__ = "sprints"
    __table_args__ = (
        Index("idx_sprints_project_id", "project_id"),
        Index("idx_sprints_deleted_at", "deleted_at"),
        Index("idx_sprints_fts", text("to_tsvector('english', coalesce(name, '') || ' ' || coalesce(goal, ''))"), postgresql_using="gin"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_sprints_project"), nullable=False)
    name = Column(String(100), nullable=False)
    goal = Column(String(500), nullable=True)
    status = Column(String(20), nullable=True, default="planned", server_default="planned")
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    actual_end_date = Column(DateTime(timezone=False), nullable=True)
    velocity = Column(Integer, nullable=True)
    created_by_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_sprints_created_by"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", foreign_keys=[project_id])
    created_by = relationship("User", foreign_keys=[created_by_id])


class SprintSnapshot(Base):
    __tablename__ = "sprint_snapshots"
    __table_args__ = (
        Index("idx_sprint_snapshot_sprint_date", "date", unique=True),
        Index("idx_sprint_snapshots_sprint_id", "sprint_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    sprint_id = Column(UUID(as_uuid=False), ForeignKey("sprints.id", name="fk_sprint_snapshots_sprint"), nullable=False)
    date = Column(Date, nullable=False)
    total_story_points = Column(Integer, nullable=False, default=0, server_default="0")
    remaining_story_points = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))

    sprint = relationship("Sprint", foreign_keys=[sprint_id])


@event.listens_for(Sprint, "before_insert")
def sprint_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())


@event.listens_for(SprintSnapshot, "before_insert")
def sprint_snapshot_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())
