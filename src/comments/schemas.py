from __future__ import annotations

# src/comments/schemas.py
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


def omit_empty(value) -> bool:
    return value is None or value == [] or value == ""


class CreateCommentsRequest(BaseModel):
    content: str = Field(..., max_length=5000, description="Comment content")
    parent_comment_id: Optional[UUID] = Field(None, description="Optional parent comment ID for replies")
    attachment_ids: List[UUID] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_attachment_collection(cls, value):
        """Accept Go's attachment_ids and the frontend's attachments objects."""
        if not isinstance(value, dict) or value.get("attachment_ids"):
            return value
        attachments = value.get("attachments")
        if not isinstance(attachments, list):
            return value

        data = dict(value)
        data["attachment_ids"] = [
            item.get("id") if isinstance(item, dict) else item
            for item in attachments
            if (item.get("id") if isinstance(item, dict) else item)
        ]
        return data


class UpdateCommentsRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000, description="Updated comment content")


class CommentedUserData(BaseModel):
    id: UUID
    task_id: Optional[UUID] = Field(default=None, exclude_if=omit_empty)
    user_story_id: Optional[UUID] = Field(default=None, exclude_if=omit_empty)
    user_id: UUID
    user_name: str
    full_name: str
    avatar_url: Optional[str] = None
    color: str
    attachments: List[CommentAttachmentResponse] = Field(
        default_factory=list, exclude_if=omit_empty
    )

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }


class ParentUserResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str
    full_name: str
    email: str
    avatar_url: Optional[str] = None
    color: str
    content: str
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }


class CommentAttachmentResponse(BaseModel):
    id: UUID
    comment_id: Optional[UUID] = None
    original_filename: str
    mime_type: str
    file_size: int
    url: Optional[str] = ""
    uploaded_by: UUID
    uploaded_at: datetime

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }


class CommentsResponse(BaseModel):
    id: UUID
    task_id: Optional[UUID] = Field(default=None, exclude_if=omit_empty)
    user_story_id: Optional[UUID] = Field(default=None, exclude_if=omit_empty)
    user_id: UUID
    user_name: str
    full_name: str
    email: str
    avatar_url: Optional[str] = None
    color: str
    content: str
    parent_comment_id: Optional[UUID] = Field(default=None, exclude_if=omit_empty)
    created_at: datetime
    updated_at: datetime
    is_deleted: bool
    parent_comment: Optional[ParentUserResponse] = Field(
        default=None, exclude_if=omit_empty
    )
    attachments: List[CommentAttachmentResponse] = Field(
        default_factory=list, exclude_if=omit_empty
    )
    replies_count: int = 0

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool

