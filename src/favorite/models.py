from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, DateTime, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Favorite:
    USER_STORY = "user_story"
    TASK = "task"


class Favorite(Base):
    __tablename__ = "favorites"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    item_type = Column(String(50), nullable=False)
    user_story_id = Column(String(36), ForeignKey("user_stories.id"), nullable=True, index=True)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    user = relationship("User", foreign_keys=[user_id])
    user_story = relationship("UserStory", foreign_keys=[user_story_id])
    task = relationship("Task", foreign_keys=[task_id])


@event.listens_for(Favorite, "before_insert")
def favorite_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())