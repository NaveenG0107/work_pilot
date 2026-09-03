# src/comments/service.py
import html
import logging
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid6 import uuid7

from src.audit.models import AuditLog
from src.auth.models import User
from src.comments.models import Comments, CommentAttachment
from src.comments.schemas import (
    CommentAttachmentResponse,
    CommentedUserData,
    CommentsResponse,
    CreateCommentsRequest,
    ParentUserResponse,
    UpdateCommentsRequest,
)
from src.middleware.rbac import has_default_permission
from src.organization.models import Role, OrphanedFile
from src.project.models import Project, ProjectMember
from src.task.models import Task
from src.user_story.models import UserStory
from src.utils.setting import get_settings
from src.utils.storage import (
    build_attachment_key,
    delete_s3_object,
    get_s3_object,
    upload_comment_attachment_to_s3,
)

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".pdf", ".txt", ".docx", ".xlsx", ".zip"
}


def sanitize_filename(filename: str) -> str:
    """Sanitizes filename by stripping directory separators and unsafe characters."""
    filename = filename.replace("\\", "/").split("/")[-1]
    sanitized = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    return sanitized or "attachment"


def sanitize_html(content: str) -> str:
    """
    Sanitizes HTML content by stripping unsafe tags/scripts while preserving safe text.
    Similar to bluemonday / Go utils.SanitizeHTML.
    """
    if not content:
        return ""
    # Strip script and style elements and contents
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.DOTALL | re.IGNORECASE)
    # Strip javascript: and on* event handlers
    cleaned = re.sub(r'href\s*=\s*["\']javascript:[^"\']*["\']', 'href="#"', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\son\w+\s*=\s*["\'][^"\']*["\']', '', cleaned, flags=re.IGNORECASE)
    # Whitelist safe tags or strip other tags if desired
    # Allow safe formatting tags: p, br, b, i, strong, em, u, a, ul, ol, li, code, pre, blockquote
    safe_tags = r"(/?(?:p|br|b|i|strong|em|u|a|ul|ol|li|code|pre|blockquote|h[1-6]|span|div)\b[^>]*)"
    cleaned = re.sub(r"<(?!" + safe_tags + r")[^>]+>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def content_with_attachment_images(
    content: str, attachments: list[CommentAttachment]
) -> str:
    """Render linked image attachments in comment HTML, matching the Go response."""
    rendered = content or ""
    image_tags: list[str] = []
    for attachment in attachments:
        if not (attachment.mime_type or "").lower().startswith("image/"):
            continue
        url = attachment.url or ""
        if not url or url in rendered:
            continue
        separator = "&" if "?" in url else "?"
        source = f"{url}{separator}attachment_id={attachment.id}"
        image_tags.append(
            f'<p><img src="{html.escape(source, quote=True)}" '
            f'alt="{html.escape(attachment.original_filename, quote=True)}"></p>'
        )
    return rendered + "".join(image_tags)


class CommentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve_task_id(
        self,
        task_id_or_key: str,
        organization_id: str | None,
    ) -> str:
        """Resolve a Task UUID or key within the authenticated organization."""
        identifier = str(task_id_or_key).strip()
        if not identifier:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task ID or Task Key is required",
            )
        if not organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization context required",
            )

        task_id = (
            await self.db.execute(
                select(Task.id)
                .join(Project, Project.id == Task.project_id)
                .where(
                    Task.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                    Project.organization_id == str(organization_id),
                    or_(
                        Task.id == identifier,
                        func.upper(Task.key) == identifier.upper(),
                    ),
                )
            )
        ).scalars().first()
        if task_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )
        return str(task_id)

    async def _check_permission(
        self, user: User, project_id: str, resource: str, action: str
    ) -> bool:

        if user.role and user.role.name == "super_admin":
            return False

        # 1. Check project-level member role first
        stmt = (
            select(ProjectMember)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
                ProjectMember.deleted_at.is_(None),
            )
            .options(selectinload(ProjectMember.role).selectinload(Role.permissions))
        )
        result = await self.db.execute(stmt)
        member = result.scalar_one_or_none()

        if member and member.role:
            for perm in member.role.permissions or []:
                if perm.resource == resource and perm.action == action:
                    return True
            if has_default_permission(member.role.name, resource, action):
                return True

        # 2. Check organization-level role if user is an org_admin in the project's organization
        proj_stmt = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()

        if project and user.organization_id and str(user.organization_id) == str(project.organization_id):
            if user.role and user.role.name == "org_admin":
                for perm in user.role.permissions or []:
                    if perm.resource == resource and perm.action == action:
                        return True
                if has_default_permission(user.role.name, resource, action):
                    return True

        return False

    async def validate_parent_comment(
        self,
        parent_comment_id: str,
        task_id: Optional[str],
        user_story_id: Optional[str],
        project_id: str,
        organization_id: str,
    ) -> None:

        stmt = select(Comments).where(Comments.id == parent_comment_id)
        res = await self.db.execute(stmt)
        parent_comment = res.scalar_one_or_none()

        if not parent_comment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent comment not found",
            )

        if task_id and parent_comment.task_id and str(parent_comment.task_id) != str(task_id):
            logger.error("Parent comment belongs to a different task: %s", parent_comment_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent comment belongs to a different task",
            )

        if user_story_id and parent_comment.user_story_id and str(parent_comment.user_story_id) != str(user_story_id):
            logger.error("Parent comment belongs to a different user story: %s", parent_comment_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent comment belongs to a different user story",
            )

        if str(parent_comment.project_id) != str(project_id):
            logger.error("Parent comment belongs to a different project: %s", parent_comment_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent comment belongs to a different project",
            )

        if str(parent_comment.organization_id) != str(organization_id):
            logger.error("Parent comment belongs to a different organization: %s", parent_comment_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent comment belongs to a different organization",
            )

        if parent_comment.is_deleted:
            # Check if it has active replies
            rep_stmt = select(func.count(Comments.id)).where(
                Comments.parent_comment_id == parent_comment_id,
                Comments.is_deleted.is_(False),
            )
            rep_res = await self.db.execute(rep_stmt)
            has_replies = (rep_res.scalar() or 0) > 0
            if not has_replies:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot reply to a deleted comment.",
                )

    async def create_comment(
        self,
        user_id: str,
        organization_id: str,
        payload: CreateCommentsRequest,
        task_id: Optional[str] = None,
        user_story_id: Optional[str] = None,
    ) -> CommentedUserData:
        """
        Creates a new comment or reply for a task or user story.
        """
        if not task_id and not user_story_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either Task ID or User Story ID must be provided",
            )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Determine target & Project ID
        project_id: str = ""
        resource_title: str = ""
        target_name: str = ""

        if task_id:
            task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
            task_res = await self.db.execute(task_stmt)
            task = task_res.scalar_one_or_none()
            if not task:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Task does not belong to the specified project",
                )
            project_id = str(task.project_id)
            resource_title = task.title
            target_name = "task"
        elif user_story_id:
            story_stmt = select(UserStory).where(UserStory.id == user_story_id, UserStory.deleted_at.is_(None))
            story_res = await self.db.execute(story_stmt)
            story = story_res.scalar_one_or_none()
            if not story:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User story does not belong to the specified project",
                )
            project_id = str(story.project_id)
            resource_title = story.title
            target_name = "userstory"

        # 3. Check base view permission
        can_view = await self._check_permission(user, project_id, "comments", "view")
        if not can_view:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to add comments to this project",
            )

        # 4. Check comment/add permission
        can_comment = await self._check_permission(user, project_id, "comments", "comment")
        can_add = await self._check_permission(user, project_id, "comments", "add")
        if not can_comment and not can_add:
            logger.error("User %s does not have permission to add comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to add comments to this project",
            )

        # 5. Validate parent comment if reply
        parent_comment_id_str = str(payload.parent_comment_id) if payload.parent_comment_id else None
        if parent_comment_id_str:
            await self.validate_parent_comment(
                parent_comment_id=parent_comment_id_str,
                task_id=task_id,
                user_story_id=user_story_id,
                project_id=project_id,
                organization_id=organization_id,
            )

        # 6. Sanitize and validate content
        sanitized_content = sanitize_html(payload.content)
        if not sanitized_content:
            requested_attachment_ids = [
                str(value) for value in payload.attachment_ids
            ]
            attachment_only_query = select(func.count()).select_from(
                CommentAttachment
            ).where(
                CommentAttachment.comment_id.is_(None),
                CommentAttachment.uploaded_by == user_id,
            )
            attachment_only_query = attachment_only_query.where(
                CommentAttachment.task_id == task_id
                if task_id
                else CommentAttachment.user_story_id == user_story_id
            )
            if requested_attachment_ids:
                attachment_only_query = attachment_only_query.where(
                    CommentAttachment.id.in_(requested_attachment_ids)
                )
            draft_count = int(
                (await self.db.execute(attachment_only_query)).scalar_one()
            )
            if draft_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Content cannot be empty",
                )

        # 7. Create Comment
        now = datetime.now(timezone.utc)
        comment_id = str(uuid7())
        comment = Comments(
            id=comment_id,
            task_id=task_id,
            user_story_id=user_story_id,
            user_id=user_id,
            project_id=project_id,
            organization_id=organization_id,
            content=sanitized_content,
            parent_comment_id=parent_comment_id_str,
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        self.db.add(comment)

        attachment_ids = [str(value) for value in payload.attachment_ids]
        draft_query = select(CommentAttachment).where(
            CommentAttachment.comment_id.is_(None),
            CommentAttachment.uploaded_by == user_id,
        )
        draft_query = draft_query.where(
            CommentAttachment.task_id == task_id
            if task_id
            else CommentAttachment.user_story_id == user_story_id
        )
        if attachment_ids:
            draft_query = draft_query.where(
                CommentAttachment.id.in_(attachment_ids)
            )
        else:
            # Some clients upload drafts and omit their IDs from the comment
            # payload. Claim that user's pending drafts for this task so the
            # successfully uploaded files are not invisible in the comment.
            draft_query = draft_query.order_by(
                CommentAttachment.uploaded_at.asc()
            ).limit(get_settings().attachment_max_files_count)

        draft_attachments = list(
            (await self.db.execute(draft_query)).scalars()
        )
        if attachment_ids:
            if len(draft_attachments) != len(set(attachment_ids)):
                await self.db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One or more attachments are invalid or do not belong to this task",
                )
        for attachment in draft_attachments:
            attachment.comment_id = comment_id

        # 8. Create Audit Log
        user_display = user.username or user.full_name or user.email or user_id
        detail_msg = f"{user_display} commented on the {target_name}: {resource_title} as {sanitized_content}"

        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            user_story_id=user_story_id,
            action="created",
            resource_type="comment",
            resource_id=comment_id,
            details=detail_msg,
            type="activity",
            created_at=now,
        )
        self.db.add(audit_log)

        # 9. Commit transaction
        try:
            await self.db.commit()
            await self.db.refresh(comment)
        except Exception as exc:
            await self.db.rollback()
            logger.exception("Failed to create comment: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create comment",
            ) from exc

        return CommentedUserData(
            id=UUID(comment.id),
            task_id=UUID(comment.task_id) if comment.task_id else None,
            user_story_id=UUID(comment.user_story_id) if comment.user_story_id else None,
            user_id=UUID(user.id),
            user_name=user.username,
            full_name=user.full_name,
            avatar_url=user.avatar_url or None,
            color=user.color or "#3498DB",
            attachments=[
                CommentAttachmentResponse(
                    id=UUID(a.id),
                    comment_id=UUID(comment.id),
                    original_filename=a.original_filename,
                    mime_type=a.mime_type,
                    file_size=a.file_size,
                    url=a.url or "",
                    uploaded_by=UUID(a.uploaded_by),
                    uploaded_at=a.uploaded_at,
                )
                for a in draft_attachments
            ],
        )

    async def get_comment_by_id(
        self,
        comment_id: str | None,
        user_id: str,
        organization_id: str,
        task_id: Optional[str] = None,
        user_story_id: Optional[str] = None,
    ) -> CommentsResponse:
        """
        Retrieves a comment by ID with its parent, attachments, and reply count.
        """
        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch comment with User, ParentComment, ParentComment.User, Attachments
        stmt = (
            select(Comments)
            .where(Comments.id == comment_id, Comments.deleted_at.is_(None))
            .options(
                selectinload(Comments.user),
                selectinload(Comments.parent_comment).selectinload(Comments.user),
                selectinload(Comments.attachments),
            )
        )
        res = await self.db.execute(stmt)
        comment = res.scalar_one_or_none()
        if not comment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Comment not found",
            )

        # Determine and validate project context
        project_id = str(comment.project_id)
        if task_id and (not comment.task_id or str(comment.task_id) != str(task_id)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment does not belong to the specified task",
            )
        if user_story_id and (
            not comment.user_story_id
            or str(comment.user_story_id) != str(user_story_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment does not belong to the specified user story",
            )

        # 3. Check permission
        can_view = await self._check_permission(user, project_id, "comments", "view")
        if not can_view:
            logger.error("User %s does not have permission to view comment in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this comment",
            )

        # 4. Count active replies
        rep_stmt = select(func.count(Comments.id)).where(
            Comments.parent_comment_id == comment.id,
            Comments.is_deleted.is_(False),
        )
        rep_res = await self.db.execute(rep_stmt)
        replies_count = rep_res.scalar() or 0

        # 5. Insert audit log
        now = datetime.now(timezone.utc)
        target_id_str = str(comment.task_id) if comment.task_id else (str(comment.user_story_id) if comment.user_story_id else "")
        target_name = "Task" if comment.task_id else "User Story"
        detail_msg = f"Comment viewed on {target_name} : {target_id_str}"

        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=str(comment.task_id) if comment.task_id else None,
            user_story_id=str(comment.user_story_id) if comment.user_story_id else None,
            action="viewed",
            resource_type="comment",
            resource_id=target_id_str,
            details=detail_msg,
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)
        try:
            await self.db.commit()
        except Exception as exc:
            logger.warning("Failed to commit audit log for comment view: %s", exc)

        # 6. Build response
        parent_resp = None
        if comment.parent_comment:
            p = comment.parent_comment
            parent_user = p.user
            parent_resp = ParentUserResponse(
                id=UUID(p.id),
                user_id=UUID(parent_user.id) if parent_user else UUID(p.user_id),
                user_name=parent_user.username if parent_user else "",
                full_name=parent_user.full_name if parent_user else "",
                email=parent_user.email if parent_user else "",
                avatar_url=parent_user.avatar_url if (parent_user and parent_user.avatar_url) else None,
                color=parent_user.color if (parent_user and parent_user.color) else "#3498DB",
                content=p.content,
                created_at=p.created_at,
                updated_at=p.updated_at,
                is_deleted=p.is_deleted,
            )

        attachments_resp = []
        for att in comment.attachments or []:
            attachments_resp.append(
                CommentAttachmentResponse(
                    id=UUID(att.id),
                    comment_id=UUID(att.comment_id),
                    original_filename=att.original_filename,
                    mime_type=att.mime_type,
                    file_size=att.file_size,
                    url=att.url or "",
                    uploaded_by=UUID(att.uploaded_by),
                    uploaded_at=att.uploaded_at,
                )
            )

        author = comment.user
        return CommentsResponse(
            id=UUID(comment.id),
            task_id=UUID(comment.task_id) if comment.task_id else None,
            user_story_id=UUID(comment.user_story_id) if comment.user_story_id else None,
            user_id=UUID(author.id) if author else UUID(comment.user_id),
            user_name=author.username if author else "",
            full_name=author.full_name if author else "",
            email=author.email if author else "",
            avatar_url=author.avatar_url if (author and author.avatar_url) else None,
            color=author.color if (author and author.color) else "#3498DB",
            content=content_with_attachment_images(
                comment.content, list(comment.attachments or [])
            ) if comment.task_id else comment.content,
            parent_comment_id=UUID(comment.parent_comment_id) if comment.parent_comment_id else None,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            is_deleted=comment.is_deleted,
            parent_comment=parent_resp,
            attachments=[] if comment.task_id else attachments_resp,
            replies_count=replies_count,
        )

    async def get_comments_by_parent_id(
        self,
        parent_comment_id: str,
        user_id: str,
        organization_id: str,
        page: int = 1,
        page_size: int = 10,
        task_id: Optional[str] = None,
        user_story_id: Optional[str] = None,
    ) -> Tuple[list[CommentsResponse], dict]:
        """
        Retrieves paginated replies for a parent comment.
        """
        import math

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Determine target & Project ID
        project_id: str = ""
        resource_title: str = ""
        target_name: str = ""

        if task_id:
            task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
            task_res = await self.db.execute(task_stmt)
            task = task_res.scalar_one_or_none()
            if not task:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Task does not belong to the specified project",
                )
            project_id = str(task.project_id)
            resource_title = task.title
            target_name = "task"
        elif user_story_id:
            story_stmt = select(UserStory).where(UserStory.id == user_story_id, UserStory.deleted_at.is_(None))
            story_res = await self.db.execute(story_stmt)
            story = story_res.scalar_one_or_none()
            if not story:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User story does not belong to the specified project",
                )
            project_id = str(story.project_id)
            resource_title = story.title
            target_name = "user story"
        else:
            parent_stmt = select(Comments).where(Comments.id == parent_comment_id)
            parent_res = await self.db.execute(parent_stmt)
            parent = parent_res.scalar_one_or_none()
            if not parent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Parent comment not found",
                )
            project_id = str(parent.project_id)
            target_name = "work item"

        # 3. Check permission
        can_view = await self._check_permission(user, project_id, "comments", "view")
        if not can_view:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this comment",
            )

        # 4. Count total replies
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        count_stmt = select(func.count(Comments.id)).where(
            Comments.organization_id == organization_id,
            Comments.parent_comment_id == parent_comment_id,
            Comments.deleted_at.is_(None),
        )
        if user_story_id:
            count_stmt = count_stmt.where(Comments.user_story_id == user_story_id)
        elif task_id:
            count_stmt = count_stmt.where(Comments.task_id == task_id)

        count_res = await self.db.execute(count_stmt)
        total_items = count_res.scalar() or 0

        # 5. Fetch replies ordered chronologically (ASC)
        query_stmt = (
            select(Comments)
            .where(
                Comments.organization_id == organization_id,
                Comments.parent_comment_id == parent_comment_id,
                Comments.deleted_at.is_(None),
            )
            .options(
                selectinload(Comments.user),
                selectinload(Comments.parent_comment).selectinload(Comments.user),
                selectinload(Comments.attachments),
            )
            .order_by(Comments.created_at.asc())
            .offset(offset)
            .limit(page_size)
        )
        if user_story_id:
            query_stmt = query_stmt.where(Comments.user_story_id == user_story_id)
        elif task_id:
            query_stmt = query_stmt.where(Comments.task_id == task_id)

        res = await self.db.execute(query_stmt)
        replies = res.scalars().all()

        # 6. Audit Log
        now = datetime.now(timezone.utc)
        user_name = user.username or user.full_name or user.email or user_id
        action_msg = f"Comment replies on {target_name} '{resource_title}' viewed by {user_name}"

        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            user_story_id=user_story_id,
            action="viewed",
            resource_type="comment",
            resource_id=parent_comment_id,
            details=action_msg,
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)
        try:
            await self.db.commit()
        except Exception as exc:
            logger.warning("Failed to commit audit log for reply comments view: %s", exc)

        # 7. Map reply comments
        items: list[CommentsResponse] = []
        for c in replies:
            p_resp = None
            if c.parent_comment:
                p = c.parent_comment
                pu = p.user
                p_resp = ParentUserResponse(
                    id=UUID(p.id),
                    user_id=UUID(pu.id) if pu else UUID(p.user_id),
                    user_name=pu.username if pu else "",
                    full_name=pu.full_name if pu else "",
                    email=pu.email if pu else "",
                    avatar_url=pu.avatar_url if (pu and pu.avatar_url) else None,
                    color=pu.color if (pu and pu.color) else "#3498DB",
                    content=p.content,
                    created_at=p.created_at,
                    updated_at=p.updated_at,
                    is_deleted=p.is_deleted,
                )

            attachments_resp = [
                CommentAttachmentResponse(
                    id=UUID(att.id),
                    comment_id=UUID(att.comment_id),
                    original_filename=att.original_filename,
                    mime_type=att.mime_type,
                    file_size=att.file_size,
                    url=att.url or "",
                    uploaded_by=UUID(att.uploaded_by),
                    uploaded_at=att.uploaded_at,
                )
                for att in c.attachments or []
            ]

            author = c.user
            items.append(
                CommentsResponse(
                    id=UUID(c.id),
                    task_id=UUID(c.task_id) if c.task_id else None,
                    user_story_id=UUID(c.user_story_id) if c.user_story_id else None,
                    user_id=UUID(author.id) if author else UUID(c.user_id),
                    user_name=author.username if author else "",
                    full_name=author.full_name if author else "",
                    email=author.email if author else "",
                    avatar_url=author.avatar_url if (author and author.avatar_url) else None,
                    color=author.color if (author and author.color) else "#3498DB",
                    content=content_with_attachment_images(
                        c.content, list(c.attachments or [])
                    ) if c.task_id else c.content,
                    parent_comment_id=UUID(c.parent_comment_id) if c.parent_comment_id else None,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                    is_deleted=c.is_deleted,
                    parent_comment=p_resp,
                    attachments=[] if c.task_id else attachments_resp,
                    replies_count=0,
                )
            )

        total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1
        meta = {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
        }

        return items, meta

    async def get_comments_by_task_id(
        self,
        task_id: str,
        user_id: str,
        organization_id: str,
        page: int = 1,
        page_size: int = 10,
    ) -> Tuple[list[CommentsResponse], dict]:
        """
        Retrieves paginated top-level comments for a task along with reply counts.
        """
        import math

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch task
        task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        task_res = await self.db.execute(task_stmt)
        task = task_res.scalar_one_or_none()
        if not task:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task does not belong to the specified project",
            )

        project_id = str(task.project_id)
        task_title = task.title

        # 3. Check permission
        can_view = await self._check_permission(user, project_id, "comments", "view")
        if not can_view:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this comment",
            )

        # 4. Count top-level comments (parent_comment_id IS NULL)
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        count_stmt = select(func.count(Comments.id)).where(
            Comments.organization_id == organization_id,
            Comments.task_id == task_id,
            Comments.parent_comment_id.is_(None),
            Comments.deleted_at.is_(None),
        )
        count_res = await self.db.execute(count_stmt)
        total_items = count_res.scalar() or 0

        # 5. Fetch top-level comments ordered by created_at DESC
        query_stmt = (
            select(Comments)
            .where(
                Comments.organization_id == organization_id,
                Comments.task_id == task_id,
                Comments.parent_comment_id.is_(None),
                Comments.deleted_at.is_(None),
            )
            .options(
                selectinload(Comments.user),
                selectinload(Comments.attachments),
            )
            .order_by(Comments.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        res = await self.db.execute(query_stmt)
        comments = res.scalars().all()

        # 6. Populate replies count for all returned comments
        replies_count_map = {}
        if comments:
            comment_ids = [c.id for c in comments]
            rep_stmt = (
                select(Comments.parent_comment_id, func.count(Comments.id))
                .where(
                    Comments.parent_comment_id.in_(comment_ids),
                    Comments.is_deleted.is_(False),
                )
                .group_by(Comments.parent_comment_id)
            )
            rep_res = await self.db.execute(rep_stmt)
            for p_id, count in rep_res.all():
                replies_count_map[p_id] = count

        # 7. Audit Log
        now = datetime.now(timezone.utc)
        user_name = user.username or user.full_name or user.email or user_id
        action_msg = f"Comments on task '{task_title}' viewed by {user_name}"

        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            action="viewed",
            resource_type="comment",
            resource_id=task_id,
            details=action_msg,
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)
        try:
            await self.db.commit()
        except Exception as exc:
            logger.warning("Failed to commit audit log for task comments view: %s", exc)

        # 8. Map to CommentsResponse
        items: list[CommentsResponse] = []
        for c in comments:
            attachments_resp = [
                CommentAttachmentResponse(
                    id=UUID(att.id),
                    comment_id=UUID(att.comment_id),
                    original_filename=att.original_filename,
                    mime_type=att.mime_type,
                    file_size=att.file_size,
                    url=att.url or "",
                    uploaded_by=UUID(att.uploaded_by),
                    uploaded_at=att.uploaded_at,
                )
                for att in c.attachments or []
            ]

            author = c.user
            items.append(
                CommentsResponse(
                    id=UUID(c.id),
                    task_id=UUID(c.task_id) if c.task_id else None,
                    user_story_id=UUID(c.user_story_id) if c.user_story_id else None,
                    user_id=UUID(author.id) if author else UUID(c.user_id),
                    user_name=author.username if author else "",
                    full_name=author.full_name if author else "",
                    email=author.email if author else "",
                    avatar_url=author.avatar_url if (author and author.avatar_url) else None,
                    color=author.color if (author and author.color) else "#3498DB",
                    content=content_with_attachment_images(
                        c.content, list(c.attachments or [])
                    ),
                    parent_comment_id=UUID(c.parent_comment_id) if c.parent_comment_id else None,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                    is_deleted=c.is_deleted,
                    parent_comment=None,
                    attachments=[],
                    replies_count=replies_count_map.get(c.id, 0),
                )
            )

        total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1
        meta = {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
        }

        return items, meta

    async def update_comment(
        self,
        comment_id: str,
        user_id: str,
        organization_id: str,
        payload: UpdateCommentsRequest,
        task_id: Optional[str] = None,
        user_story_id: Optional[str] = None,
    ) -> CommentedUserData:
        """
        Updates an existing comment.
        """
        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch comment
        stmt = (
            select(Comments)
            .where(Comments.id == comment_id, Comments.is_deleted.is_(False))
            .options(selectinload(Comments.attachments))
        )
        res = await self.db.execute(stmt)
        comment = res.scalar_one_or_none()
        if not comment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Comment not found",
            )

        project_id = str(comment.project_id)
        if task_id and (not comment.task_id or str(comment.task_id) != str(task_id)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment does not belong to the specified task",
            )
        if user_story_id and (
            not comment.user_story_id
            or str(comment.user_story_id) != str(user_story_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment does not belong to the specified user story",
            )

        # 3. Check base view permission & modify permission
        can_view = await self._check_permission(user, project_id, "comments", "view")
        if not can_view:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this comment",
            )

        can_modify = await self._check_permission(user, project_id, "comments", "modify")
        if not can_modify:
            logger.error("User %s does not have permission to modify comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify comments on this task/story",
            )

        # 4. Check ownership: Only the comment author can update it
        if str(comment.user_id) != str(user_id):
            logger.error("User %s tried to update comment owned by %s", user_id, comment.user_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only update your own comments",
            )

        # 5. Sanitize HTML and validate content
        sanitized_content = sanitize_html(payload.content)
        if not sanitized_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content cannot be empty",
            )

        # 6. Target details for audit log
        resource_title = ""
        target_name = ""
        if comment.task_id:
            task_stmt = select(Task).where(Task.id == comment.task_id, Task.deleted_at.is_(None))
            task_res = await self.db.execute(task_stmt)
            task = task_res.scalar_one_or_none()
            if task:
                resource_title = task.title
            target_name = f"task: {resource_title or comment.task_id}"
        elif comment.user_story_id:
            story_stmt = select(UserStory).where(UserStory.id == comment.user_story_id, UserStory.deleted_at.is_(None))
            story_res = await self.db.execute(story_stmt)
            story = story_res.scalar_one_or_none()
            if story:
                resource_title = story.title
            target_name = f"userstory: {resource_title or comment.user_story_id}"

        # 7. Update Comment
        now = datetime.now(timezone.utc)
        old_content = comment.content
        comment.content = sanitized_content
        comment.updated_at = now

        # 8. Audit Log
        user_name = user.username or user.full_name or user.email or user_id
        if old_content != sanitized_content:
            detail_msg = f"{user_name} updated comment on the {target_name}: content changed from '{old_content}' to '{sanitized_content}'"
        else:
            detail_msg = f"{user_name} updated comment on the {target_name}"

        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=str(comment.task_id) if comment.task_id else None,
            user_story_id=str(comment.user_story_id) if comment.user_story_id else None,
            action="updated",
            resource_type="comment",
            resource_id=comment_id,
            details=detail_msg,
            type="activity",
            created_at=now,
        )
        self.db.add(audit_log)

        # 9. Commit transaction
        try:
            await self.db.commit()
            await self.db.refresh(comment)
        except Exception as exc:
            await self.db.rollback()
            logger.exception("Failed to update comment: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update comment",
            ) from exc

        return CommentedUserData(
            id=UUID(comment.id),
            task_id=UUID(comment.task_id) if comment.task_id else None,
            user_story_id=UUID(comment.user_story_id) if comment.user_story_id else None,
            user_id=UUID(user.id),
            user_name=user.username,
            full_name=user.full_name,
            avatar_url=user.avatar_url or None,
            color=user.color or "#3498DB",
            attachments=[
                CommentAttachmentResponse(
                    id=UUID(a.id),
                    comment_id=UUID(a.comment_id) if a.comment_id else None,
                    original_filename=a.original_filename,
                    mime_type=a.mime_type,
                    file_size=a.file_size,
                    url=a.url or "",
                    uploaded_by=UUID(a.uploaded_by),
                    uploaded_at=a.uploaded_at,
                )
                for a in comment.attachments
            ],
        )

    async def delete_comment(
        self,
        comment_id: str,
        user_id: str,
        organization_id: str,
        task_id: Optional[str] = None,
        user_story_id: Optional[str] = None,
    ) -> dict[str, str]:
        """
        Deletes a comment. If the comment has replies, it is soft-deleted to preserve thread hierarchy;
        otherwise it is removed.
        """
        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch comment
        stmt = select(Comments).where(
            Comments.id == comment_id,
            Comments.deleted_at.is_(None),
        )
        res = await self.db.execute(stmt)
        comment = res.scalar_one_or_none()
        if not comment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Comment not found",
            )

        if str(comment.organization_id) != str(organization_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to delete this comment",
            )

        project_id = str(comment.project_id)
        if task_id and (not comment.task_id or str(comment.task_id) != str(task_id)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment does not belong to the specified task",
            )
        if user_story_id and (
            not comment.user_story_id
            or str(comment.user_story_id) != str(user_story_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment does not belong to the specified user story",
            )

        # 3. Check base view permission & delete permission
        can_view = await self._check_permission(user, project_id, "comments", "view")
        if not can_view:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this comment",
            )

        can_delete = await self._check_permission(user, project_id, "comments", "delete")
        if not can_delete:
            logger.error("User %s does not have permission to delete comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to delete comments on this task/story",
            )

        # 4. Check ownership: Only the comment author can delete it
        if str(comment.user_id) != str(user_id):
            logger.error("User %s tried to delete comment owned by %s", user_id, comment.user_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete your own comments",
            )

        # 5. Target details for audit log
        resource_title = ""
        target_name = ""
        if comment.task_id:
            task_stmt = select(Task).where(Task.id == comment.task_id, Task.deleted_at.is_(None))
            task_res = await self.db.execute(task_stmt)
            task = task_res.scalar_one_or_none()
            if task:
                resource_title = task.title
            target_name = f"task: {resource_title or comment.task_id}"
        elif comment.user_story_id:
            story_stmt = select(UserStory).where(UserStory.id == comment.user_story_id, UserStory.deleted_at.is_(None))
            story_res = await self.db.execute(story_stmt)
            story = story_res.scalar_one_or_none()
            if story:
                resource_title = story.title
            target_name = f"userstory: {resource_title or comment.user_story_id}"

        # 6. Preserve deleted parents that still have replies; otherwise hide
        # the row from normal queries, matching Go's Mark/Delete split.
        now = datetime.now(timezone.utc)
        active_replies = int(
            (
                await self.db.execute(
                    select(func.count(Comments.id)).where(
                        Comments.parent_comment_id == comment_id,
                        Comments.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )
        comment.is_deleted = True
        comment.deleted_at = None if active_replies else now
        comment.updated_at = now

        # 7. Audit Log
        user_name = user.username or user.full_name or user.email or user_id
        detail_msg = f"Comment on the {target_name} was deleted by {user_name}"

        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=str(comment.task_id) if comment.task_id else None,
            user_story_id=str(comment.user_story_id) if comment.user_story_id else None,
            action="deleted",
            resource_type="comment",
            resource_id=comment_id,
            details=detail_msg,
            type="activity",
            created_at=now,
        )
        self.db.add(audit_log)

        # 8. Commit transaction
        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            logger.exception("Failed to delete comment: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete comment",
            ) from exc

        return {"comment_id": comment_id}

    async def upload_comment_attachments(
        self,
        comment_id: Optional[str],
        task_id: Optional[str],
        user_id: str,
        organization_id: str,
        files: list[UploadFile],
        user_story_id: Optional[str] = None,
    ) -> list[CommentAttachmentResponse]:
        """
        Uploads one or more attachments to a task comment.
        """
        logger.info(
            "Uploading %d attachment(s) to comment %s (task: %s) by user %s",
            len(files),
            comment_id,
            task_id,
            user_id,
        )

        # 1. Validate files count (uses centralized Settings from src.utils.setting)
        settings = get_settings()
        max_files = settings.attachment_max_files_count
        max_size_mb = settings.attachment_max_file_size_mb
        max_size_bytes = max_size_mb * 1024 * 1024

        logger.info("Max files: %d, Max size: %d MB (%d bytes)", max_files, max_size_mb, max_size_bytes)
        if not files:
            logger.error("No files provided in request for comment %s", comment_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing file(s) in request payload (use form-data keys 'file' or 'files')",
            )

        if len(files) > max_files:
            logger.error("Too many files in request: %d (max %d allowed)", len(files), max_files)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum of {max_files} files can be uploaded per request.",
            )

        # 2. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 3. Existing-comment uploads validate ownership; draft uploads have
        # no comment yet and are scoped directly to the task.
        if comment_id is not None:
            stmt = select(Comments).where(
                Comments.id == comment_id,
                Comments.is_deleted.is_(False),
            )
            res = await self.db.execute(stmt)
            comment = res.scalar_one_or_none()
            if not comment:
                logger.error("Comment %s not found or is deleted", comment_id)
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Comment not found",
                )

            wrong_target = (
                task_id
                and (not comment.task_id or str(comment.task_id) != str(task_id))
            ) or (
                user_story_id
                and (
                    not comment.user_story_id
                    or str(comment.user_story_id) != str(user_story_id)
                )
            )
            if wrong_target:
                logger.error("Comment %s does not belong to task %s", comment_id, task_id)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Comment does not belong to the specified work item",
                )

        # 4. Resolve the task or user-story project context.
        if task_id:
            target = (
                await self.db.execute(
                    select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
            target_error = "Task does not belong to the specified project"
        elif user_story_id:
            target = (
                await self.db.execute(
                    select(UserStory).where(
                        UserStory.id == user_story_id,
                        UserStory.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            target_error = "User story does not belong to the specified project"
        else:
            target = None
            target_error = "Either Task ID or User Story ID must be provided"
        if not target:
            raise HTTPException(status_code=400, detail=target_error)

        project_id = str(target.project_id)
        permission_action = "view" if comment_id is not None else "add"
        can_access = await self._check_permission(
            user, project_id, "comments", permission_action
        )
        if not can_access:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this project",
            )

        existing_count = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(CommentAttachment)
                    .where(
                        CommentAttachment.comment_id == comment_id
                        if comment_id is not None
                        else (
                            CommentAttachment.comment_id.is_(None)
                            & (
                                (CommentAttachment.task_id == task_id)
                                if task_id
                                else (CommentAttachment.user_story_id == user_story_id)
                            )
                            & (CommentAttachment.uploaded_by == user_id)
                        )
                    )
                )
            ).scalar_one()
        )
        if existing_count + len(files) > max_files:
            logger.warning(
                "Comment %s attachment limit exceeded: existing=%d incoming=%d max=%d",
                comment_id,
                existing_count,
                len(files),
                max_files,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum of {max_files} attachments are allowed per comment.",
            )

        # 5. Read and validate the whole batch before writing anything to S3.
        validated_files: list[tuple[UploadFile, str, bytes]] = []
        for file in files:
            orig_filename = file.filename or "file"
            ext = pathlib.Path(orig_filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"Unsupported file content type for extension '{ext}'.",
                )
            file_content = await file.read()
            if len(file_content) > max_size_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File {orig_filename} exceeds the maximum allowed size of {max_size_mb} MB.",
                )
            validated_files.append((file, orig_filename, file_content))

        # 6. Upload and stage metadata.
        now = datetime.now(timezone.utc)
        created_attachments: list[CommentAttachment] = []
        uploaded_paths: list[str] = []

        for file, orig_filename, file_content in validated_files:
            file_size = len(file_content)

            att_id = str(uuid7())
            sanitized_name = sanitize_filename(orig_filename)
            folder_id = comment_id or task_id or user_story_id
            storage_path = build_attachment_key("comments", folder_id, sanitized_name)
            stored_name = storage_path.rsplit("/", 1)[-1]
            content_type = file.content_type or "application/octet-stream"

            # Upload directly to S3 storage 
            try:
                url = upload_comment_attachment_to_s3(
                    file_bytes=file_content,
                    key=storage_path,
                    content_type=content_type,
                )
                uploaded_paths.append(storage_path)
            except Exception as s3_err:
                for uploaded_path in uploaded_paths:
                    try:
                        delete_s3_object(uploaded_path)
                    except Exception:
                        logger.warning("Failed to roll back S3 object %s", uploaded_path)
                await self.db.rollback()
                logger.exception("Failed to upload attachment %s to S3: %s", orig_filename, s3_err)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to upload file to storage. Please try again.",
                ) from s3_err

            attachment = CommentAttachment(
                id=att_id,
                comment_id=comment_id,
                task_id=task_id,
                user_story_id=user_story_id,
                original_filename=orig_filename,
                stored_filename=stored_name,
                mime_type=content_type,
                file_size=file_size,
                storage_path=storage_path,
                url=url,
                uploaded_by=user_id,
                uploaded_at=now,
            )
            self.db.add(attachment)
            created_attachments.append(attachment)

            audit_log = AuditLog(
                id=str(uuid7()),
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                task_id=task_id,
                user_story_id=user_story_id,
                action="uploaded",
                resource_type="comment_attachment",
                resource_id=att_id,
                details=(
                    f"Attachment {orig_filename} uploaded to comment {comment_id}"
                    if comment_id is not None
                    else f"Attachment {orig_filename} uploaded for draft task comment {task_id}"
                ),
                type="audit",
                created_at=now,
            )
            self.db.add(audit_log)

        # 7. Commit transaction
        try:
            await self.db.commit()
            for att in created_attachments:
                await self.db.refresh(att)
            logger.info("Successfully uploaded %d attachment(s) for comment %s", len(created_attachments), comment_id)
        except Exception as exc:
            await self.db.rollback()
            for uploaded_path in uploaded_paths:
                try:
                    delete_s3_object(uploaded_path)
                except Exception:
                    logger.warning("Failed to roll back S3 object %s", uploaded_path)
            logger.exception("Failed to persist comment attachments to database: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save attachment metadata. Please try again.",
            ) from exc

        return [
            CommentAttachmentResponse(
                id=UUID(a.id),
                comment_id=UUID(a.comment_id) if a.comment_id else None,
                original_filename=a.original_filename,
                mime_type=a.mime_type,
                file_size=a.file_size,
                url=a.url,
                uploaded_by=UUID(a.uploaded_by),
                uploaded_at=a.uploaded_at,
            )
            for a in created_attachments
        ]

    async def get_comment_attachments(
        self,
        comment_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
    ) -> list[CommentAttachmentResponse]:
        """
        Retrieves all attachments associated with a comment.
        """
        logger.info(
            "Fetching attachments for comment %s (task: %s) by user %s",
            comment_id,
            task_id,
            user_id,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch comment
        stmt = select(Comments).where(Comments.id == comment_id, Comments.is_deleted.is_(False))
        res = await self.db.execute(stmt)
        comment = res.scalar_one_or_none()
        if not comment:
            logger.error("Comment %s not found or is deleted", comment_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Comment not found",
            )

        if not comment.task_id or str(comment.task_id) != str(task_id):
            logger.error("Comment %s does not belong to task %s", comment_id, task_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment does not belong to the specified task",
            )

        # 3. Fetch task and check permissions
        task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        task_res = await self.db.execute(task_stmt)
        task = task_res.scalar_one_or_none()
        if not task:
            logger.error("Task %s not found", task_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task does not belong to the specified project",
            )

        project_id = str(task.project_id)
        can_access = await self._check_permission(user, project_id, "comments", "view")
        if not can_access:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this project",
            )

        # 4. Fetch attachments ordered by uploaded_at asc
        att_stmt = (
            select(CommentAttachment)
            .where(CommentAttachment.comment_id == comment_id)
            .order_by(CommentAttachment.uploaded_at.asc())
        )
        att_res = await self.db.execute(att_stmt)
        attachments = att_res.scalars().all()

        # 5. Create activity audit log (best-effort)
        now = datetime.now(timezone.utc)
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            action="viewed",
            resource_type="comment_attachment",
            resource_id=comment_id,
            details=f"User {user.email} viewed attachments from comment {comment.id}",
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)

        try:
            await self.db.commit()
        except Exception as audit_err:
            await self.db.rollback()
            logger.warning("Failed to save audit log for get_comment_attachments (best-effort): %s", audit_err)

        logger.info("Retrieved %d attachment(s) for comment %s", len(attachments), comment_id)
        return [
            CommentAttachmentResponse(
                id=UUID(a.id),
                comment_id=UUID(a.comment_id),
                original_filename=a.original_filename,
                mime_type=a.mime_type,
                file_size=a.file_size,
                url=a.url,
                uploaded_by=UUID(a.uploaded_by),
                uploaded_at=a.uploaded_at,
            )
            for a in attachments
        ]

    async def download_comment_attachment(
        self,
        attachment_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
        comment_id: Optional[str] = None,
        draft_only: bool = False,
    ) -> Tuple[any, str, str, int]:
        """
        Validates access and returns attachment stream, original_filename, mime_type, and size.
        """
        logger.info(
            "Request to download attachment %s (task: %s) by user %s",
            attachment_id,
            task_id,
            user_id,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch attachment
        att_stmt = select(CommentAttachment).where(CommentAttachment.id == attachment_id)
        att_res = await self.db.execute(att_stmt)
        attachment = att_res.scalar_one_or_none()
        if not attachment:
            logger.error("Comment attachment %s not found", attachment_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attachment not found",
            )

        if draft_only:
            if (
                attachment.comment_id is not None
                or str(attachment.task_id) != str(task_id)
                or str(attachment.uploaded_by) != str(user_id)
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Attachment not found",
                )
            comment = None
        else:
            comment_stmt = select(Comments).where(
                Comments.id == attachment.comment_id,
                Comments.is_deleted.is_(False),
            )
            comment_res = await self.db.execute(comment_stmt)
            comment = comment_res.scalar_one_or_none()
            if not comment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Comment not found",
                )
            if comment_id and str(comment.id) != str(comment_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Attachment not found",
                )

        if comment is not None and (not comment.task_id or str(comment.task_id) != str(task_id)):
            logger.error("Attachment %s does not belong to task %s", attachment_id, task_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Attachment does not belong to the specified task",
            )

        # 4. Fetch task and check permissions
        task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        task_res = await self.db.execute(task_stmt)
        task = task_res.scalar_one_or_none()
        if not task:
            logger.error("Task %s not found", task_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task does not belong to the specified project",
            )

        project_id = str(task.project_id)
        can_access = await self._check_permission(user, project_id, "comments", "view")
        if not can_access:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this project",
            )

        # 5. Fetch file stream from S3
        stream, content_length, content_type = get_s3_object(attachment.storage_path)

        # 6. Create activity audit log (best-effort)
        now = datetime.now(timezone.utc)
        task_key = task.key if hasattr(task, "key") and task.key else task_id
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            action="download",
            resource_type="comment_attachment",
            resource_id=task_id,
            details=(
                f"User {user.email} downloaded attachment {attachment.original_filename}"
                if draft_only
                else f"User {user.email} downloaded attachment {attachment.original_filename} from comment {task_key}"
            ),
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)

        try:
            await self.db.commit()
        except Exception as audit_err:
            await self.db.rollback()
            logger.warning("Failed to save audit log for download_comment_attachment (best-effort): %s", audit_err)

        final_mime = attachment.mime_type or content_type
        final_size = attachment.file_size if attachment.file_size else content_length
        return stream, attachment.original_filename, final_mime, final_size

    async def delete_comment_attachment(
        self,
        attachment_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
        comment_id: Optional[str] = None,
        draft_only: bool = False,
    ) -> None:
        """
        Deletes a comment attachment if authorized.
        """
        logger.info(
            "Request to delete attachment %s (task: %s) by user %s",
            attachment_id,
            task_id,
            user_id,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch attachment
        att_stmt = select(CommentAttachment).where(CommentAttachment.id == attachment_id)
        att_res = await self.db.execute(att_stmt)
        attachment = att_res.scalar_one_or_none()
        if not attachment:
            logger.error("Comment attachment %s not found", attachment_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attachment not found",
            )

        if draft_only:
            if (
                attachment.comment_id is not None
                or str(attachment.task_id) != str(task_id)
                or str(attachment.uploaded_by) != str(user_id)
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Attachment not found",
                )
            comment = None
        else:
            comment_stmt = select(Comments).where(
                Comments.id == attachment.comment_id,
                Comments.is_deleted.is_(False),
            )
            comment_res = await self.db.execute(comment_stmt)
            comment = comment_res.scalar_one_or_none()
            if not comment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Comment not found",
                )
            if comment_id and str(comment.id) != str(comment_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Attachment not found",
                )

        if comment is not None and (not comment.task_id or str(comment.task_id) != str(task_id)):
            logger.error("Attachment %s does not belong to task %s", attachment_id, task_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Attachment does not belong to the specified task",
            )

        # 4. Fetch task and check permissions
        task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        task_res = await self.db.execute(task_stmt)
        task = task_res.scalar_one_or_none()
        if not task:
            logger.error("Task %s not found", task_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task does not belong to the specified project",
            )

        project_id = str(task.project_id)
        can_view = await self._check_permission(user, project_id, "comments", "view")
        if not can_view:
            logger.error("User %s does not have permission to view comments in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this project",
            )

        # 5. Check delete authorization: uploader OR comment author OR comments:modify (e.g. Org Admin / Project Manager)
        is_uploader = str(attachment.uploaded_by) == str(user_id)
        is_comment_author = comment is not None and str(comment.user_id) == str(user_id)
        has_modify = await self._check_permission(user, project_id, "comments", "modify")

        if not (is_uploader or is_comment_author or has_modify):
            logger.error("User %s is not authorized to delete attachment %s", user_id, attachment_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the uploader, comment author, Project Managers, or Organization Administrators can delete this attachment",
            )

        storage_path = attachment.storage_path
        orig_filename = attachment.original_filename
        now = datetime.now(timezone.utc)

        # 6. Delete attachment from DB and record orphaned file for cleanup
        await self.db.delete(attachment)
        orphan = OrphanedFile(
            id=str(uuid7()),
            storage_path=storage_path,
            attempts=0,
            available_at=now,
            created_at=now,
        )
        self.db.add(orphan)

        # 7. Create activity audit log
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            action="deleted",
            resource_type="comment_attachment",
            resource_id=attachment_id,
                details=(
                    f"Attachment {orig_filename} deleted"
                    if draft_only
                    else f"Attachment {orig_filename} deleted from comment {comment.id}"
                ),
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)

        try:
            await self.db.commit()
            logger.info("Successfully deleted attachment %s from database", attachment_id)
        except Exception as exc:
            await self.db.rollback()
            logger.exception("Failed to delete comment attachment %s: %s", attachment_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete attachment metadata",
            ) from exc

        # 8. Best-effort direct S3 deletion
        try:
            delete_s3_object(storage_path)
        except Exception as s3_err:
            logger.warning("Failed direct S3 deletion of %s (orphan record will retry): %s", storage_path, s3_err)









