import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.project.schema import (
    PROJECT_STATUSES,
    CreateProjectMemberRequest,
    CreateProjectRequest,
    LegacyProjectIDResponse,
    PaginatedSuccessResponse,
    ProjectActivityResponse,
    ProjectDetail,
    ProjectIDResponse,
    ProjectMemberResponse,
    ProjectSummary,
    SuccessResponse,
    SuccessWithoutDataResponse,
    UpdateProjectMemberRequest,
    UpdateProjectRequest,
    UserProjectRoleResponse,
    UserProjectsResponse,
)
from src.project.service import ProjectService, ProjectServiceError
from src.utils.core import (
    GoJSONResponse as JSONResponse,
    authenticate_request,
    bearer_scheme,
    require_jwt,
)


class GoValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            auth_error = await authenticate_request(request)
            if auth_error is not None:
                try:
                    auth_detail = json.loads(auth_error.body)
                except (TypeError, ValueError, json.JSONDecodeError):
                    auth_detail = {}
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "success": False,
                        "error": {
                            "code": auth_detail.get("code", "UNAUTHORIZED"),
                            "status_code": status.HTTP_401_UNAUTHORIZED,
                            "message": auth_detail.get(
                                "message", "Authentication required"
                            ),
                        },
                    },
                )
            try:
                return await original(request)
            except RequestValidationError as exc:
                message = validation_message(exc, request.method)
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "status_code": 400,
                            "message": message,
                        },
                    },
                )

        return handler


router = APIRouter(
    prefix="/project",
    route_class=GoValidationRoute,
    dependencies=[Depends(bearer_scheme)],
    default_response_class=JSONResponse,
)


def field_label(field: object) -> str:
    parts = str(field).split("_")
    return " ".join("ID" if part.lower() == "id" else part.capitalize() for part in parts)


def validation_message(exc: RequestValidationError, method: str) -> str:
    if method == "GET":
        return "Invalid query parameters"

    error = exc.errors()[0]
    error_type = error.get("type", "")
    field = field_label(error.get("loc", ("field",))[-1])
    context = error.get("ctx") or {}

    if error_type == "missing":
        return f"{field} is required."
    if error_type == "string_too_short":
        return f"{field} must be at least {context.get('min_length')} characters."
    if error_type == "string_too_long":
        return f"{field} must not exceed {context.get('max_length')} characters."
    if error_type == "too_short":
        return f"{field} must be at least {context.get('min_length')} characters."
    if error_type == "json_invalid":
        return "Invalid JSON request body format."
    if error_type.endswith("_type"):
        expected = error_type.removesuffix("_type")
        return f"Invalid data type for {field}. Expected {expected}."
    return "Invalid request payload."


def get_project_service(db: AsyncSession = Depends(get_db)) -> ProjectService:
    return ProjectService(db)


def success(
    message: str,
    data: Any = None,
    *,
    code: int = status.HTTP_200_OK,
    meta: Any = None,
) -> JSONResponse:
    body: dict[str, Any] = {"success": True, "status_code": code, "message": message}
    if data is not None:
        body["data"] = data
    if meta is not None:
        body["meta"] = meta
    return JSONResponse(status_code=code, content=body)


def failure(exc: Exception) -> JSONResponse:
    if isinstance(exc, ProjectServiceError):
        code, error_code, message = exc.status_code, exc.code, exc.message
    else:
        code, error_code, message = (
            500,
            "INTERNAL_SERVER_ERROR",
            "Something went wrong. Please try again later.",
        )
    return JSONResponse(
        status_code=code,
        content={
            "success": False,
            "error": {"code": error_code, "status_code": code, "message": message},
        },
    )


def validated_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProjectServiceError(400, "BAD_REQUEST", "Invalid ID format") from exc


def validated_query_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProjectServiceError(
            400, "VALIDATION_ERROR", "Invalid query parameters"
        ) from exc


def normalized_sort(sort_by: str, sort_order: str) -> tuple[str, str]:
    sort_by = sort_by.strip() or "created_at"
    sort_order = sort_order.strip().upper()
    if sort_order not in {"ASC", "DESC"}:
        sort_order = "DESC"
    return sort_by, sort_order


def validate_status(value: str) -> str:
    if value and value not in PROJECT_STATUSES:
        raise ProjectServiceError(
            400,
            "BAD_REQUEST",
            "Invalid status. Allowed values: active, archived, on_hold, completed, cancelled, planning",
        )
    return value


def dumped(value: Any) -> Any:
    if isinstance(value, list):
        return [item.model_dump(mode="json", by_alias=True) for item in value]
    return value.model_dump(mode="json", by_alias=True)


@router.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[ProjectIDResponse],
    tags=["Projects"],
)
@require_jwt
async def create_project(
    body: CreateProjectRequest,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        user_id = request.state.user_id #'6a3b86a9-e93f-4a60-8cc7-e432b59bd2dc' 
        org_id = request.state.organization_id #'6a3b86a9-e93f-4a60-8cc7-e432b59bd2dd' 
        project_id = await service.create(body, user_id, org_id)
        return success(
            "Successfully Created Project", {"project_id": project_id}, code=201
        )
    except Exception as exc:
        return failure(exc)


@router.patch(
    "/update/{project_id}",
    response_model=SuccessResponse[ProjectIDResponse],
    tags=["Projects"],
)
@require_jwt
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_id = request.state.user_id #'6a3b86a9-e93f-4a60-8cc7-e432b59bd2dc' 
        org_id = request.state.organization_id #'6a3b86a9-e93f-4a60-8cc7-e432b59bd2dd' 
        await service.update(project_id, body, user_id, org_id)
        return success("Updated Project successfully", {"project_id": project_id})
    except Exception as exc:
        return failure(exc)


@router.get(
    "/get",
    response_model=PaginatedSuccessResponse[list[ProjectSummary]],
    tags=["Projects"],
)
@require_jwt
async def get_projects(
    request: Request,
    page: int = 1,
    page_size: int = 10,
    name: str = "",
    status_filter: str = Query(
        "", alias="status", json_schema_extra={"enum": PROJECT_STATUSES}
    ),
    sort_by: str = "created_at",
    sort_order: str = "DESC",
    fields: str = "",
    include_sprints: bool = False,
    service: ProjectService = Depends(get_project_service),
):
    try:
        user_id = request.state.user_id
        org_id = request.state.organization_id
        role = request.state.role
        page, page_size = max(1, page), page_size if page_size > 0 else 10
        status_filter = validate_status(status_filter)
        sort_by, sort_order = normalized_sort(sort_by, sort_order)
        data, meta = await service.list_projects(
            organization_id=org_id,
            user_id=user_id,
            user_role=role,
            page=page,
            page_size=page_size,
            name=name,
            status=status_filter,
            include_sprints=include_sprints,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        rows = dumped(data)
        if fields:
            wanted = {field.strip() for field in fields.split(",") if field.strip()}
            rows = [
                {key: row[key] for key in sorted(row) if key in wanted}
                for row in rows
            ]
        return success(
            "Projects retrieved successfully.", rows, meta=meta.model_dump(mode="json")
        )
    except Exception as exc:
        return failure(exc)


@router.get(
    "/all-projects",
    response_model=PaginatedSuccessResponse[list[ProjectSummary]],
    tags=["Projects"],
)
@require_jwt
async def get_all_projects(
    request: Request,
    page: int = 1,
    page_size: int = 10,
    search: str = "",
    name: str = "",
    status_filter: str = Query(
        "", alias="status", json_schema_extra={"enum": PROJECT_STATUSES}
    ),
    organization_id: str | None = None,
    created_by: str | None = None,
    include_sprints: bool = False,
    sort_by: str = "created_at",
    sort_order: str = "DESC",
    service: ProjectService = Depends(get_project_service),
):
    try:
        page, page_size = max(1, page), page_size if page_size > 0 else 10
        status_filter = validate_status(status_filter)
        sort_by, sort_order = normalized_sort(sort_by, sort_order)
        if organization_id:
            organization_id = validated_query_uuid(organization_id)
        if created_by:
            created_by = validated_query_uuid(created_by)
        data, meta = await service.list_projects(
            organization_id=organization_id,
            user_id=None,
            page=page,
            page_size=page_size,
            name=name,
            status=status_filter,
            search=search,
            created_by=created_by,
            include_sprints=include_sprints,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return success(
            "All projects retrieved successfully.",
            dumped(data),
            meta=meta.model_dump(mode="json"),
        )
    except Exception as exc:
        return failure(exc)


@router.post(
    "/add-members",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessWithoutDataResponse,
    tags=["Project Members"],
)
@require_jwt
async def add_members(
    body: CreateProjectMemberRequest,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        user_id, org_id = request.state.user_id, request.state.organization_id
        await service.add_members(body, user_id, org_id)
        return success("Successfully Added Project Member", code=201)
    except Exception as exc:
        return failure(exc)


@router.get(
    "/members/{project_id}",
    response_model=PaginatedSuccessResponse[list[ProjectMemberResponse]],
    tags=["Project Members"],
)
@require_jwt
async def get_members(
    project_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 10,
    name: str = "",
    service: ProjectService = Depends(get_project_service),
):
    try:
        project_id = validated_uuid(project_id)
        org_id = request.state.organization_id
        page, page_size = max(1, page), page_size if page_size > 0 else 10
        data, meta = await service.members(project_id, org_id, page, page_size, name)
        return success(
            "Project members retrieved successfully.",
            dumped(data),
            meta=meta.model_dump(mode="json"),
        )
    except Exception as exc:
        return failure(exc)


@router.delete(
    "/{project_id}/member/{user_id}",
    response_model=SuccessResponse[LegacyProjectIDResponse],
    tags=["Project Members"],
)
@require_jwt
async def remove_member(
    project_id: str,
    user_id: str,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_id = validated_uuid(user_id)
        actor = request.state.user_id
        org_id = request.state.organization_id
        await service.remove_member(project_id, user_id, actor, org_id)
        return success("Project member removed successfully.", {"ProjectID": project_id})
    except Exception as exc:
        return failure(exc)


@router.patch(
    "/{project_id}/member/{user_id}",
    response_model=SuccessResponse[LegacyProjectIDResponse],
    tags=["Projects"],
)
@require_jwt
async def update_member(
    project_id: str,
    user_id: str,
    body: UpdateProjectMemberRequest,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_id = validated_uuid(user_id)
        actor = request.state.user_id
        org_id = request.state.organization_id
        await service.update_member(project_id, user_id, body, actor, org_id)
        return success("Project member updated successfully", {"ProjectID": project_id})
    except Exception as exc:
        return failure(exc)


@router.get(
    "/{project_id}/activity/{type}",
    response_model=PaginatedSuccessResponse[list[ProjectActivityResponse]],
    tags=["Projects"],
)
@require_jwt
async def project_activity(
    project_id: str,
    request: Request,
    type: str = Path(..., json_schema_extra={"enum": ["view", "activity"]}),
    page: int = 1,
    page_size: int = 10,
    action: str = "",
    resource_type: str = Query(
        "",
        json_schema_extra={
            "enum": ["project", "task", "userstory", "sprint", "comment"]
        },
    ),
    resource_id: str = "",
    task_id: str = "",
    user_story_id: str = "",
    sprint_id: str = "",
    user_id: str = "",
    start_date: str = "",
    end_date: str = "",
    service: ProjectService = Depends(get_project_service),
):
    try:
        project_id = validated_uuid(project_id)
        if user_id:
            try:
                user_id = str(uuid.UUID(user_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ProjectServiceError(
                    400, "BAD_REQUEST", "Invalid UserID filter format"
                ) from exc
        for field_name, value in (
            ("task_id", task_id),
            ("user_story_id", user_story_id),
            ("sprint_id", sprint_id),
        ):
            if value:
                try:
                    parsed = str(uuid.UUID(value))
                except (ValueError, TypeError, AttributeError):
                    parsed = ""
                if field_name == "task_id":
                    task_id = parsed
                elif field_name == "user_story_id":
                    user_story_id = parsed
                else:
                    sprint_id = parsed
        org_id = request.state.organization_id
        page, page_size = max(1, page), page_size if page_size > 0 else 10
        data, meta = await service.activities(
            project_id,
            org_id,
            page,
            page_size,
            type,
            action,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            task_id=task_id,
            user_story_id=user_story_id,
            sprint_id=sprint_id,
            start_date=start_date,
            end_date=end_date,
        )
        return success(
            "Project activity history retrieved successfully.",
            dumped(data),
            meta=meta.model_dump(mode="json"),
        )
    except Exception as exc:
        return failure(exc)


@router.get(
    "/{project_id}/detail",
    response_model=SuccessResponse[ProjectDetail],
    tags=["Projects"],
)
@require_jwt
async def project_detail(
    project_id: str,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        user_id = request.state.user_id
        org_id = request.state.organization_id
        return success(
            "Project retrieved successfully.",
            dumped(await service.detail(project_id, user_id, org_id)),
        )
    except Exception as exc:
        return failure(exc)


@router.delete(
    "/{project_id}",
    response_model=SuccessResponse[ProjectIDResponse],
    tags=["Projects"],
)
@require_jwt
async def delete_project(
    project_id: str,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        user_id = request.state.user_id
        org_id = request.state.organization_id
        project_id = validated_uuid(project_id)
        await service.delete(project_id, user_id, org_id)
        return success("Project deleted successfully", {"project_id": project_id})
    except Exception as exc:
        return failure(exc)


@router.get(
    "/user/{user_id}",
    response_model=SuccessResponse[UserProjectsResponse],
    tags=["Projects"],
)
@require_jwt
async def projects_by_user(
    user_id: str,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        user_id = validated_uuid(user_id)
        org_id = request.state.organization_id
        caller_id = request.state.user_id
        caller_role = request.state.role
        return success(
            "Project retrieved successfully.",
            dumped(
                await service.user_projects(
                    user_id,
                    org_id,
                    caller_id=caller_id,
                    caller_role=caller_role,
                )
            ),
        )
    except Exception as exc:
        return failure(exc)


@router.get(
    "/recent",
    response_model=SuccessResponse[UserProjectsResponse],
    tags=["Projects"],
)
@require_jwt
async def recent_projects(
    request: Request, service: ProjectService = Depends(get_project_service)
):
    try:
        user_id = request.state.user_id
        org_id = request.state.organization_id
        return success(
            "Recent projects retrieved successfully.",
            dumped(await service.user_projects(user_id, org_id, recent=True)),
        )
    except Exception as exc:
        return failure(exc)


@router.get(
    "/{project_id}/user-role",
    response_model=SuccessResponse[UserProjectRoleResponse],
    tags=["Projects"],
)
@require_jwt
async def project_role(
    project_id: str,
    request: Request,
    service: ProjectService = Depends(get_project_service),
):
    try:
        project_id = validated_uuid(project_id)
        user_id = request.state.user_id
        org_id = request.state.organization_id
        return success(
            "User project role retrieved successfully.",
            dumped(await service.user_role(project_id, user_id, org_id)),
        )
    except Exception as exc:
        return failure(exc)
