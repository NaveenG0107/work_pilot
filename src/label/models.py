from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, DateTime, UniqueConstraint, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Label(Base):
    __tablename__ = "labels"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(30), nullable=False)
    color = Column(String(7), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    project = relationship("Project", foreign_keys=[project_id])

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="idx_project_label_name"),
    )


@event.listens_for(Label, "before_insert")
def label_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())