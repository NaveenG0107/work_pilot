from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, DateTime, Integer, Date, UniqueConstraint, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Sprint(Base):
    __tablename__ = "sprints"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    goal = Column(String(500), nullable=True)
    status = Column(String(20), nullable=True, default="planned")
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    actual_end_date = Column(DateTime, nullable=True)
    velocity = Column(Integer, nullable=True)
    created_by_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    project = relationship("Project", foreign_keys=[project_id])
    created_by = relationship("User", foreign_keys=[created_by_id])


class SprintSnapshot(Base):
    __tablename__ = "sprint_snapshots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    sprint_id = Column(String(36), ForeignKey("sprints.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    total_story_points = Column(Integer, nullable=False, default=0)
    remaining_story_points = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    sprint = relationship("Sprint", foreign_keys=[sprint_id])

    __table_args__ = (
        UniqueConstraint("sprint_id", "date", name="idx_sprint_snapshot_sprint_date"),
    )


@event.listens_for(Sprint, "before_insert")
def sprint_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())


@event.listens_for(SprintSnapshot, "before_insert")
def sprint_snapshot_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())