from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, DateTime, Index, text, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (
        Index("idx_favorites_deleted_at", "deleted_at"),
        Index("idx_favorites_user_id", "user_id"),
        Index("idx_favorites_user_story_id", "user_story_id"),
        Index("idx_favorites_task_id", "task_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_favorites_user"), nullable=False)
    item_type = Column(String(50), nullable=False)
    user_story_id = Column(UUID(as_uuid=False), ForeignKey("user_stories.id", name="fk_favorites_user_story"), nullable=True)
    task_id = Column(UUID(as_uuid=False), ForeignKey("tasks.id", name="fk_favorites_task"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    user_story = relationship("UserStory", foreign_keys=[user_story_id])
    task = relationship("Task", foreign_keys=[task_id])


@event.listens_for(Favorite, "before_insert")
def favorite_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())
