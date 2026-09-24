from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, DateTime, Integer, Index, func, text, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class CustomStatus(Base):
    __tablename__ = "custom_statuses"
    __table_args__ = (
        Index("idx_custom_statuses_project_id", "project_id"),
        Index("idx_custom_statuses_deleted_at", "deleted_at"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_custom_statuses_project"), nullable=False)
    name = Column(String(50), nullable=False)
    color = Column(String(7), nullable=False)
    display_order = Column(Integer, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False, server_default="false")
    is_final = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", foreign_keys=[project_id])


@event.listens_for(CustomStatus, "before_insert")
def custom_status_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())
