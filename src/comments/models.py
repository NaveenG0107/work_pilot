from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, DateTime, BigInteger, Index, event, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, foreign

from uuid6 import uuid7

from src.database import Base


class Comments(Base):
    __tablename__ = "comments"
    __table_args__ = (
        Index("idx_comments_deleted_at", "deleted_at"),
        Index("idx_comments_organization_id", "organization_id"),
        Index("idx_comments_parent_comment_id", "parent_comment_id"),
        Index("idx_comments_project_id", "project_id"),
        Index("idx_comments_task_id", "task_id"),
        Index("idx_comments_user_id", "user_id"),
        Index("idx_comments_user_story_id", "user_story_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    task_id = Column(UUID(as_uuid=False), nullable=True)
    user_story_id = Column(UUID(as_uuid=False), nullable=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", name="fk_comments_user"), nullable=False)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", name="fk_comments_project"), nullable=False)
    organization_id = Column(UUID(as_uuid=False), nullable=False)
    content = Column(Text, nullable=False)
    parent_comment_id = Column(UUID(as_uuid=False), ForeignKey("comments.id", name="fk_comments_parent_comment"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_deleted = Column(Boolean, nullable=True, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    task = relationship("Task", primaryjoin="foreign(Comments.task_id) == Task.id", foreign_keys=[task_id])
    user_story = relationship("UserStory", primaryjoin="foreign(Comments.user_story_id) == UserStory.id", foreign_keys=[user_story_id])
    project = relationship("Project", foreign_keys=[project_id])
    organization = relationship("Organization", primaryjoin="foreign(Comments.organization_id) == Organization.id", foreign_keys=[organization_id])
    parent_comment = relationship("Comments", remote_side=[id], foreign_keys=[parent_comment_id])
    attachments = relationship("CommentAttachment", back_populates="comment", foreign_keys="CommentAttachment.comment_id")

    replies_count = None


class CommentAttachment(Base):
    __tablename__ = "comment_attachments"
    __table_args__ = (
        Index("idx_comment_attachments_comment_id", "comment_id"),
        Index("idx_comment_attachments_task_id", "task_id"),
        Index("idx_comment_attachments_uploaded_by", "uploaded_by"),
        Index("idx_comment_attachments_user_story_id", "user_story_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    comment_id = Column(UUID(as_uuid=False), ForeignKey("comments.id", name="fk_comments_attachments"), nullable=True)
    task_id = Column(UUID(as_uuid=False), nullable=True)
    user_story_id = Column(UUID(as_uuid=False), nullable=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    storage_path = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default="", server_default=text("''::text"))
    uploaded_by = Column(UUID(as_uuid=False), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    comment = relationship("Comments", back_populates="attachments", foreign_keys=[comment_id])
    task = relationship("Task", primaryjoin="foreign(CommentAttachment.task_id) == Task.id", foreign_keys=[task_id])
    user_story = relationship("UserStory", primaryjoin="foreign(CommentAttachment.user_story_id) == UserStory.id", foreign_keys=[user_story_id])
    uploader = relationship("User", primaryjoin="foreign(CommentAttachment.uploaded_by) == User.id", foreign_keys=[uploaded_by])


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
