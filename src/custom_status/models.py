from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, DateTime, Integer, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class CustomStatus(Base):
    __tablename__ = "custom_statuses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    color = Column(String(7), nullable=False)
    display_order = Column(Integer, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    is_final = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    project = relationship("Project", foreign_keys=[project_id])


@event.listens_for(CustomStatus, "before_insert")
def custom_status_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())