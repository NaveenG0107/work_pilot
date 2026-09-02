from typing import List

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status

from src.config import get_logger
from src.user_story.schema import (
    CreateCommentRequest,
    CreateUserStoryRequest,
    ReorderUserStoriesRequest,
    UpdateCommentRequest,
    UpdateUserStoryRequest,
    UpdateUserStoryStatusAssignmentRequest,
    UserStoryFilter,
)
from src.user_story.service import UserStoryService
from src.utils.core import (
    GoJSONResponse as JSONResponse,
    bearer_scheme,
    require_jwt,
)
from src.utils.user_story_helper import (
    GoValidationRoute,
    dumped,
    failure,
    get_user_story_service,
    success,
    validated_uuid,
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/projects",
    route_class=GoValidationRoute,
    dependencies=[Depends(bearer_scheme)],
    default_response_class=JSONResponse,
)


@router.post("/{project_id}/user-stories", status_code=status.HTTP_201_CREATED, tags=["User Stories"])
@require_jwt
async def create_user_story(
    project_id: str,
    body: CreateUserStoryRequest,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Creating user story '%s' for project_id=%s, user_id=%s", body.title, project_id, user_id)

        story = await service.create(body, project_id, user_id, org_id)

        logger.info("Successfully created user story id=%s", story.id)

        return success(
            "Successfully Created User Story", dumped(story), code=201
        )

    except Exception as exc:
        logger.warning("Error creating user story: %s", exc)
        return failure(exc)


@router.get("/{project_id}/user-stories", tags=["User Stories"])
@require_jwt
async def get_user_stories(
    project_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 10,
    sort_by: str = "created_at",
    sort_order: str = "DESC",
    status: str = "",
    assignee_id: str = "",
    reporter_id: str = "",
    sprint_id: str = "",
    priority: str = "",
    search: str = "",
    fields: str = "",
    serial_number: int | None = None,
    sequence_number: int | None = None,
    is_unassigned_story: bool = False,
    is_closed: bool | None = None,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Fetching user stories for project_id=%s, page=%d, page_size=%d", project_id, page, page_size)

        effective_serial = sequence_number if serial_number is None else serial_number

        filter_ = UserStoryFilter(
            page=page if page > 0 else 1,
            page_size=page_size if page_size > 0 else 10,
            sort_by=sort_by.strip() or "created_at",
            sort_order=(sort_order.strip() or "DESC").upper(),
            status=status.strip(),
            assignee_id=assignee_id.strip() or None,
            reporter_id=reporter_id.strip() or None,
            sprint_id=sprint_id.strip() or None,
            priority=priority.strip(),
            search=search,
            fields=fields,
            serial_number=effective_serial,
            sequence_number=sequence_number,
            is_unassigned_story=is_unassigned_story,
            is_closed=is_closed,
        )

        data, meta = await service.list(project_id, user_id, org_id, filter_)
        rows = dumped(data)

        if fields:
            wanted = {field.strip() for field in fields.split(",") if field.strip()}
            rows = [
                {key: row[key] for key in sorted(row) if key in wanted}
                for row in rows
            ]

        logger.info("Successfully fetched %d user stories for project_id=%s", len(rows), project_id)

        return success(
            "User Stories retrieved successfully",
            rows,
            meta=meta.model_dump(mode="json"),
        )

    except Exception as exc:
        logger.warning("Error fetching user stories: %s", exc)
        return failure(exc)


@router.get("/{project_id}/user-stories/{user_story_id}", tags=["User Stories"])
@require_jwt
async def get_user_story(
    project_id: str,
    user_story_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Fetching user story id=%s for project_id=%s", user_story_id, project_id)

        story = await service.get_by_id(user_story_id, project_id, user_id, org_id)

        logger.info("Successfully fetched user story id=%s", user_story_id)

        return success("User Story retrieved successfully", dumped(story))

    except Exception as exc:
        logger.warning("Error fetching user story: %s", exc)
        return failure(exc)


@router.patch("/{project_id}/user-stories/{user_story_id}", tags=["User Stories"])
@require_jwt
async def update_user_story(
    project_id: str,
    user_story_id: str,
    body: UpdateUserStoryRequest,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Updating user story id=%s for project_id=%s", user_story_id, project_id)

        story = await service.update(body, user_story_id, project_id, user_id, org_id)

        logger.info("Successfully updated user story id=%s", user_story_id)

        return success("Successfully Updated User Story", dumped(story))

    except Exception as exc:
        logger.warning("Error updating user story: %s", exc)
        return failure(exc)


@router.patch("/{project_id}/user-stories/{user_story_id}/status", tags=["User Stories"])
@require_jwt
async def update_user_story_status(
    project_id: str,
    user_story_id: str,
    body: UpdateUserStoryStatusAssignmentRequest,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Updating status for user story id=%s in project_id=%s", user_story_id, project_id)

        story = await service.update_status(body, user_story_id, project_id, user_id, org_id)

        logger.info("Successfully updated status for user story id=%s", user_story_id)

        return success("Successfully Updated User Story Status", dumped(story))

    except Exception as exc:
        logger.warning("Error updating user story status: %s", exc)
        return failure(exc)


@router.patch("/{project_id}/user-stories/reorder", tags=["User Stories"])
@require_jwt
async def reorder_user_stories(
    project_id: str,
    body: ReorderUserStoriesRequest,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Reordering %d user stories for project_id=%s", len(body.story_ids), project_id)

        await service.reorder(body, project_id, user_id, org_id)

        logger.info("Successfully reordered user stories for project_id=%s", project_id)

        return success("User Stories reordered successfully")

    except Exception as exc:
        logger.warning("Error reordering user stories: %s", exc)
        return failure(exc)


@router.delete("/{project_id}/user-stories/{user_story_id}", tags=["User Stories"])
@require_jwt
async def delete_user_story(
    project_id: str,
    user_story_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Deleting user story id=%s from project_id=%s", user_story_id, project_id)

        await service.delete(user_story_id, project_id, user_id, org_id)

        logger.info("Successfully deleted user story id=%s", user_story_id)

        return success("Successfully Deleted User Story")

    except Exception as exc:
        logger.warning("Error deleting user story: %s", exc)
        return failure(exc)


@router.post("/{project_id}/user-stories/{user_story_id}/favorite", status_code=status.HTTP_201_CREATED, tags=["Favorites"])
@require_jwt
async def add_user_story_favorite(
    project_id: str,
    user_story_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id

        logger.info("Adding user story id=%s to favorites for user_id=%s", user_story_id, user_id)

        fav = await service.add_favorite(user_id, project_id, user_story_id)

        logger.info("Successfully added user story id=%s to favorites", user_story_id)

        return success("User Story added to favorites successfully", dumped(fav), code=201)

    except Exception as exc:
        logger.warning("Error adding user story to favorites: %s", exc)
        return failure(exc)


@router.delete("/{project_id}/user-stories/{user_story_id}/favorite", tags=["Favorites"])
@require_jwt
async def remove_user_story_favorite(
    project_id: str,
    user_story_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id

        logger.info("Removing user story id=%s from favorites for user_id=%s", user_story_id, user_id)

        res = await service.remove_favorite(user_id, project_id, user_story_id)

        logger.info("Successfully removed user story id=%s from favorites", user_story_id)

        return success("User Story removed from favorites successfully", dumped(res))

    except Exception as exc:
        logger.warning("Error removing user story from favorites: %s", exc)
        return failure(exc)


@router.post("/{project_id}/user-stories/{user_story_id}/attachments", status_code=status.HTTP_201_CREATED, tags=["User Story Attachments"])
@require_jwt
async def upload_user_story_attachment(
    project_id: str,
    user_story_id: str,
    request: Request,
    files: List[UploadFile] = File(...),
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Uploading %d attachment(s) for user_story_id=%s, project_id=%s, user_id=%s", len(files), user_story_id, project_id, user_id)

        attachments = await service.upload_attachments(user_story_id, project_id, user_id, org_id, files)

        logger.info("Successfully uploaded %d attachment(s) for user_story_id=%s", len(attachments), user_story_id)

        return success("Attachments uploaded successfully", dumped(attachments), code=201)

    except Exception as exc:
        logger.warning("Error uploading user story attachments: %s", exc)
        return failure(exc)


@router.get("/{project_id}/user-stories/{user_story_id}/attachments", tags=["User Story Attachments"])
@require_jwt
async def get_user_story_attachments(
    project_id: str,
    user_story_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Fetching attachments for user_story_id=%s, project_id=%s, user_id=%s", user_story_id, project_id, user_id)

        attachments = await service.get_attachments(user_story_id, project_id, user_id, org_id)

        logger.info("Successfully fetched %d attachment(s) for user_story_id=%s", len(attachments), user_story_id)

        return success("Attachments retrieved successfully", dumped(attachments))

    except Exception as exc:
        logger.warning("Error retrieving user story attachments: %s", exc)
        return failure(exc)


@router.get("/{project_id}/user-stories/{user_story_id}/attachments/{attachment_id}/download", tags=["User Story Attachments"])
@require_jwt
async def download_user_story_attachment(
    project_id: str,
    user_story_id: str,
    attachment_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        attachment_id = validated_uuid(attachment_id)
        user_id = request.state.user_id

        logger.info("Downloading attachment_id=%s for user_story_id=%s, project_id=%s, user_id=%s", attachment_id, user_story_id, project_id, user_id)
        
        content, filename, media_type = await service.download_attachment(attachment_id, user_story_id, project_id, user_id)

        logger.info("Successfully downloaded attachment_id=%s ('%s')", attachment_id, filename)

        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition, Content-Type",
            },
        )

    except Exception as exc:
        logger.warning("Error downloading user story attachment: %s", exc)
        return failure(exc)


@router.delete("/{project_id}/user-stories/{user_story_id}/attachments/{attachment_id}", tags=["User Story Attachments"])
@require_jwt
async def delete_user_story_attachment(
    project_id: str,
    user_story_id: str,
    attachment_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        attachment_id = validated_uuid(attachment_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Deleting attachment_id=%s from user_story_id=%s, project_id=%s, user_id=%s", attachment_id, user_story_id, project_id, user_id)

        await service.delete_attachment(attachment_id, user_story_id, project_id, user_id, org_id)

        logger.info("Successfully deleted attachment_id=%s", attachment_id)

        return success("Attachment deleted successfully")

    except Exception as exc:
        logger.warning("Error deleting user story attachment: %s", exc)
        return failure(exc)


@router.post("/{project_id}/user-stories/{user_story_id}/comments", status_code=status.HTTP_201_CREATED, tags=["Comments"])
@require_jwt
async def create_user_story_comment(
    project_id: str,
    user_story_id: str,
    body: CreateCommentRequest,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Creating comment on user_story_id=%s, project_id=%s, user_id=%s", user_story_id, project_id, user_id)

        comment = await service.create_comment(body, user_story_id, project_id, user_id, org_id)

        logger.info("Successfully created comment id=%s for user_story_id=%s", comment.id, user_story_id)

        return success("Comment created successfully", dumped(comment), code=201)

    except Exception as exc:
        logger.warning("Error creating user story comment: %s", exc)
        return failure(exc)


@router.get("/{project_id}/user-stories/{user_story_id}/comments", tags=["Comments"])
@require_jwt
async def get_user_story_comments(
    project_id: str,
    user_story_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 10,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Fetching comments for user_story_id=%s, project_id=%s, page=%d, page_size=%d", user_story_id, project_id, page, page_size)

        comments, meta = await service.get_comments(user_story_id, project_id, user_id, org_id, page, page_size)

        logger.info("Successfully fetched %d top-level comment(s) for user_story_id=%s", len(comments), user_story_id)

        return success("Comments retrieved successfully", dumped(comments), meta=meta.model_dump(mode="json"))

    except Exception as exc:
        logger.warning("Error retrieving user story comments: %s", exc)
        return failure(exc)


@router.get("/{project_id}/user-stories/{user_story_id}/comments/{comment_id}", tags=["Comments"])
@require_jwt
async def get_user_story_comment(
    project_id: str,
    user_story_id: str,
    comment_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        comment_id = validated_uuid(comment_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Fetching comment_id=%s for user_story_id=%s, project_id=%s", comment_id, user_story_id, project_id)

        comment = await service.get_comment_by_id(comment_id, user_story_id, project_id, user_id, org_id)

        logger.info("Successfully fetched comment_id=%s", comment_id)

        return success("Comment fetched successfully", dumped(comment))

    except Exception as exc:
        logger.warning("Error fetching user story comment: %s", exc)
        return failure(exc)


@router.get("/{project_id}/user-stories/{user_story_id}/comments/replies/{parent_comment_id}", tags=["Comments"])
@require_jwt
async def get_user_story_comment_replies(
    project_id: str,
    user_story_id: str,
    parent_comment_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 10,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        parent_comment_id = validated_uuid(parent_comment_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Fetching replies for parent_comment_id=%s, user_story_id=%s, page=%d, page_size=%d", parent_comment_id, user_story_id, page, page_size)

        comments, meta = await service.get_comment_replies(parent_comment_id, user_story_id, project_id, user_id, org_id, page, page_size)

        logger.info("Successfully fetched %d reply comment(s) for parent_comment_id=%s", len(comments), parent_comment_id)

        return success("Comments received successfully", dumped(comments), meta=meta.model_dump(mode="json"))

    except Exception as exc:
        logger.warning("Error fetching user story comment replies: %s", exc)
        return failure(exc)


@router.patch("/{project_id}/user-stories/{user_story_id}/comments/{comment_id}", tags=["Comments"])
@require_jwt
async def update_user_story_comment(
    project_id: str,
    user_story_id: str,
    comment_id: str,
    body: UpdateCommentRequest,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        comment_id = validated_uuid(comment_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Updating comment_id=%s for user_story_id=%s, project_id=%s, user_id=%s", comment_id, user_story_id, project_id, user_id)

        comment = await service.update_comment(body, comment_id, user_story_id, project_id, user_id, org_id)

        logger.info("Successfully updated comment_id=%s", comment_id)

        return success("Comment updated successfully", dumped(comment))

    except Exception as exc:
        logger.warning("Error updating user story comment: %s", exc)
        return failure(exc)


@router.delete("/{project_id}/user-stories/{user_story_id}/comments/{comment_id}", tags=["Comments"])
@require_jwt
async def delete_user_story_comment(
    project_id: str,
    user_story_id: str,
    comment_id: str,
    request: Request,
    service: UserStoryService = Depends(get_user_story_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_story_id = validated_uuid(user_story_id)
        comment_id = validated_uuid(comment_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id

        logger.info("Deleting comment_id=%s from user_story_id=%s, project_id=%s, user_id=%s", comment_id, user_story_id, project_id, user_id)

        await service.delete_comment(comment_id, user_story_id, project_id, user_id, org_id)

        logger.info("Successfully deleted comment_id=%s", comment_id)

        return success("Comment deleted successfully", data={"comment_id": comment_id})

    except Exception as exc:
        logger.warning("Error deleting user story comment: %s", exc)
        return failure(exc)
