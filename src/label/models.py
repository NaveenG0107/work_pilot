from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, Index, String, DateTime, event, literal
from sqlalchemy.sql import sqltypes
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (
        Index("idx_labels_deleted_at", "deleted_at"),
        Index("idx_labels_project_id", "project_id"),
        Index("idx_project_label_name", "project_id", "name", unique=True),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_labels_project"), nullable=False)
    name = Column(String(30), nullable=False)
    color = Column(String(7), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", foreign_keys=[project_id])


@event.listens_for(Label, "before_insert")
def label_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())
