import logging
import urllib.parse
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.deps import get_current_user
from src.comments.schemas import (
    CommentedUserData,
    CreateCommentsRequest,
    UpdateCommentsRequest,
)
from src.comments.service import CommentService
from src.database import get_db
from src.response import error, success

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Comments"])


def get_comment_service(db: AsyncSession = Depends(get_db)) -> CommentService:
    return CommentService(db)


def validate_uuid(value: str, param_name: str = "ID") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {param_name} format",
        )


@router.post("/task/{task_id}/comments", status_code=status.HTTP_201_CREATED)
async def create_task_comment(
    payload: CreateCommentsRequest,
    task_id: str = Path(..., description="Task ID"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Creates a new comment for the specified task.
    To create a reply, provide the parent_comment_id of an existing comment.
    """
    valid_task_id = validate_uuid(task_id, "task_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.create_comment(
            user_id=user_id,
            organization_id=organization_id,
            payload=payload,
            task_id=valid_task_id,
        )
        return success(
            message="Comment created successfully",
            data=res,
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in create_task_comment: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/task/{task_id}/comments", status_code=status.HTTP_200_OK)
async def get_task_comments(
    task_id: str = Path(..., description="Task ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Page size"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Get paginated top-level comments for a task along with their replies count.
    """
    valid_task_id = validate_uuid(task_id, "task_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        items, meta = await service.get_comments_by_task_id(
            task_id=valid_task_id,
            user_id=user_id,
            organization_id=organization_id,
            page=page,
            page_size=page_size,
        )
        return success(
            message="Comments received successfully",
            data=items,
            meta=meta,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_task_comments: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/task/{task_id}/comments/{comment_id}", status_code=status.HTTP_200_OK)
async def get_task_comment_by_id(
    task_id: str = Path(..., description="Task ID"),
    comment_id: str = Path(..., description="Comment ID"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Get a task comment by ID along with its parent, attachments, and replies count.
    """
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_comment_id = validate_uuid(comment_id, "comment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.get_comment_by_id(
            comment_id=valid_comment_id,
            user_id=user_id,
            organization_id=organization_id,
            task_id=valid_task_id,
        )
        return success(
            message="Comment fetched successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_task_comment_by_id: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/task/{task_id}/comments/replies/{parent_comment_id}", status_code=status.HTTP_200_OK)
async def get_task_comment_replies(
    task_id: str = Path(..., description="Task ID"),
    parent_comment_id: str = Path(..., description="Parent Comment ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Page size"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Get all replies for a parent comment in a task.
    """
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_parent_id = validate_uuid(parent_comment_id, "parent_comment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        items, meta = await service.get_comments_by_parent_id(
            parent_comment_id=valid_parent_id,
            user_id=user_id,
            organization_id=organization_id,
            page=page,
            page_size=page_size,
            task_id=valid_task_id,
        )
        return success(
            message="Comments received successfully",
            data=items,
            meta=meta,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_task_comment_replies: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.patch("/task/{task_id}/comments/{comment_id}", status_code=status.HTTP_200_OK)
async def update_task_comment(
    payload: UpdateCommentsRequest,
    task_id: str = Path(..., description="Task ID"),
    comment_id: str = Path(..., description="Comment ID"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Update an existing task comment.
    """
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_comment_id = validate_uuid(comment_id, "comment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.update_comment(
            comment_id=valid_comment_id,
            user_id=user_id,
            organization_id=organization_id,
            payload=payload,
            task_id=valid_task_id,
        )
        return success(
            message="Comment updated successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in update_task_comment: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.delete("/task/{task_id}/comments/{comment_id}", status_code=status.HTTP_200_OK)
async def delete_task_comment(
    task_id: str = Path(..., description="Task ID"),
    comment_id: str = Path(..., description="Comment ID"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Delete a task comment.
    """
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_comment_id = validate_uuid(comment_id, "comment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.delete_comment(
            comment_id=valid_comment_id,
            user_id=user_id,
            organization_id=organization_id,
            task_id=valid_task_id,
        )
        return success(
            message="Comment deleted successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in delete_task_comment: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.post("/task/{task_id}/comments/{comment_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_comment_attachments(
    task_id: str = Path(..., description="Task ID"),
    comment_id: str = Path(..., description="Comment ID"),
    file: Optional[list[UploadFile]] = File(None, description="File(s) to upload under key 'file'"),
    files: Optional[list[UploadFile]] = File(None, description="File(s) to upload under key 'files'"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Upload one or more attachments associated with a comment.
    """
    logger.info("Received request to upload attachment(s) for comment %s on task %s", comment_id, task_id)
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_comment_id = validate_uuid(comment_id, "comment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for uploading attachment")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for uploading attachment")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    all_uploaded: list[UploadFile] = []
    if file:
        all_uploaded.extend([f for f in file if f.filename])
    if files:
        all_uploaded.extend([f for f in files if f.filename])

    if not all_uploaded:
        logger.error("No file(s) found in request for comment %s", comment_id)
        return error(
            message="Missing file(s) in request payload (use form-data keys 'file' or 'files')",
            status_code=status.HTTP_400_BAD_REQUEST,
            code="BAD_REQUEST",
        )

    try:
        res = await service.upload_comment_attachments(
            comment_id=valid_comment_id,
            task_id=valid_task_id,
            user_id=user_id,
            organization_id=organization_id,
            files=all_uploaded,
        )
        return success(
            message="Attachments uploaded successfully",
            data=res,
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in upload_comment_attachments: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in upload_comment_attachments: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/task/{task_id}/comments/{comment_id}/attachments", status_code=status.HTTP_200_OK)
async def get_comment_attachments(
    task_id: str = Path(..., description="Task ID"),
    comment_id: str = Path(..., description="Comment ID"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Retrieve all attachments associated with a comment.
    """
    logger.info("Received request to get attachments for comment %s on task %s", comment_id, task_id)
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_comment_id = validate_uuid(comment_id, "comment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for retrieving comment attachments")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for retrieving comment attachments")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.get_comment_attachments(
            comment_id=valid_comment_id,
            task_id=valid_task_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        return success(
            message="Attachments retrieved successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_comment_attachments: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_comment_attachments: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/task/{task_id}/comments/{comment_id}/attachments/{attachment_id}/download")
async def download_comment_attachment(
    task_id: str = Path(..., description="Task ID"),
    comment_id: str = Path(..., description="Comment ID"),
    attachment_id: str = Path(..., description="Attachment ID"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Download a comment attachment file stream.
    """
    logger.info("Received request to download attachment %s on comment %s (task: %s)", attachment_id, comment_id, task_id)
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_comment_id = validate_uuid(comment_id, "comment_id")
    valid_attachment_id = validate_uuid(attachment_id, "attachment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for downloading attachment")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for downloading attachment")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        stream, filename, mime_type, size = await service.download_comment_attachment(
            attachment_id=valid_attachment_id,
            task_id=valid_task_id,
            user_id=user_id,
            organization_id=organization_id,
        )

        clean_filename = filename.replace("\n", "").replace("\r", "").replace('"', "")
        encoded_filename = urllib.parse.quote(clean_filename)

        headers = {
            "Content-Disposition": f'attachment; filename="{clean_filename}"; filename*=UTF-8\'\'{encoded_filename}',
            "Content-Type": mime_type,
            "Content-Length": str(size),
        }

        def iterfile():
            try:
                for chunk in stream.iter_chunks(chunk_size=65536):
                    yield chunk
            except Exception as stream_err:
                logger.error("Error reading file stream for attachment %s: %s", valid_attachment_id, stream_err)
            finally:
                if hasattr(stream, "close"):
                    stream.close()

        return StreamingResponse(
            iterfile(),
            media_type=mime_type,
            headers=headers,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in download_comment_attachment: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in download_comment_attachment: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.delete("/task/{task_id}/comments/{comment_id}/attachments/{attachment_id}", status_code=status.HTTP_200_OK)
async def delete_comment_attachment(
    task_id: str = Path(..., description="Task ID"),
    comment_id: str = Path(..., description="Comment ID"),
    attachment_id: str = Path(..., description="Attachment ID"),
    current_user: dict = Depends(get_current_user),
    service: CommentService = Depends(get_comment_service),
):
    """
    Delete comment attachment if authorized.
    """
    logger.info("Received request to delete attachment %s on comment %s (task: %s)", attachment_id, comment_id, task_id)
    valid_task_id = validate_uuid(task_id, "task_id")
    valid_comment_id = validate_uuid(comment_id, "comment_id")
    valid_attachment_id = validate_uuid(attachment_id, "attachment_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for deleting attachment")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for deleting attachment")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        await service.delete_comment_attachment(
            attachment_id=valid_attachment_id,
            task_id=valid_task_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        return success(
            message="Attachment deleted successfully",
            data=None,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in delete_comment_attachment: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in delete_comment_attachment: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )






