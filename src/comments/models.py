from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, DateTime, BigInteger, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class Comments(Base):
    __tablename__ = "comments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=True, index=True)
    user_story_id = Column(String(36), ForeignKey("user_stories.id"), nullable=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    organization_id = Column(String(36), nullable=False, index=True)
    content = Column(Text, nullable=False)
    parent_comment_id = Column(String(36), ForeignKey("comments.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    user = relationship("User", foreign_keys=[user_id])
    task = relationship("Task", foreign_keys=[task_id])
    user_story = relationship("UserStory", foreign_keys=[user_story_id])
    project = relationship("Project", foreign_keys=[project_id])
    parent_comment = relationship("Comments", remote_side=[id], foreign_keys=[parent_comment_id])
    attachments = relationship("CommentAttachment", back_populates="comment", foreign_keys="CommentAttachment.comment_id")

    replies_count = None


class CommentAttachment(Base):
    __tablename__ = "comment_attachments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    comment_id = Column(String(36), ForeignKey("comments.id"), nullable=True, index=True)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=True, index=True)
    user_story_id = Column(String(36), ForeignKey("user_stories.id"), nullable=True, index=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    storage_path = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default="")
    uploaded_by = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    comment = relationship("Comments", back_populates="attachments", foreign_keys=[comment_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])


@event.listens_for(Comments, "before_insert")
def comments_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())


@event.listens_for(CommentAttachment, "before_insert")
def comment_attachment_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.uploaded_at:
        target.uploaded_at = datetime.now(timezone.utc)
