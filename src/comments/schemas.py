# src/comments/schemas.py
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class CreateCommentsRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000, description="Comment content")
    parent_comment_id: Optional[UUID] = Field(None, description="Optional parent comment ID for replies")


class UpdateCommentsRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000, description="Updated comment content")


class CommentedUserData(BaseModel):
    id: UUID
    task_id: Optional[UUID] = None
    user_story_id: Optional[UUID] = None
    user_id: UUID
    user_name: str
    full_name: str
    avatar_url: Optional[str] = None
    color: str

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
    comment_id: UUID
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
    task_id: Optional[UUID] = None
    user_story_id: Optional[UUID] = None
    user_id: UUID
    user_name: str
    full_name: str
    email: str
    avatar_url: Optional[str] = None
    color: str
    content: str
    parent_comment_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool
    parent_comment: Optional[ParentUserResponse] = None
    attachments: List[CommentAttachmentResponse] = Field(default_factory=list)
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

