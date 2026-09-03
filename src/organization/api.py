# src/organization/api.py
"""
FastAPI router for the organization module.
All routes require a valid access token; service-level checks enforce org-level authorization.
"""

import os
from html import escape
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.auth.deps import get_current_user

from src.utils import storage as storage_service
from src.organization.schemas import (
    CreateRoleRequest,
    GlobalMemberListFilter,
    InviteOrganizationMemberRequest,
    OrganizationFilterRequest,
    OrganizationMemberListFilter,
    UpdateOrganizationStatusRequest,
    UpdateRoleRequest,
    UserRoleRequest,
    UserStatusRequest,
)
from src.organization.service import OrganizationService
from src.public.models import Country
from src.response import error, success

router = APIRouter(tags=["Organizations"])

_INVITATION_TEMPLATES = Path(__file__).resolve().parents[1] / "utils" / "templates"


def _invitation_page(template_name: str, **values: str) -> HTMLResponse:
    """Render the same embedded invitation pages used by the Go backend."""
    content = (_INVITATION_TEMPLATES / template_name).read_text(encoding="utf-8")
    for key, value in values.items():
        content = content.replace(f"{{{{.{key}}}}}", escape(value, quote=True))
    return HTMLResponse(content=content, status_code=200)


async def _service(db: AsyncSession = Depends(get_db)) -> OrganizationService:
    return OrganizationService(db)


# --------------------------------------------------------------- delete org


@router.delete("/organization/delete")
async def delete_organization(
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    await service.delete_organization(org_id, UUID(current_user["user_id"]))
    return success("Organization deleted successfully", data={"organizationID": str(org_id)})


# ------------------------------------------------------------- update org


@router.patch("/organization/update")
async def update_organization(
    name: Optional[str] = Form(None),
    domain: Optional[str] = Form(None),
    team_size: Optional[str] = Form(None),
    country_id: Optional[str] = Form(None),
    logo: Optional[UploadFile] = File(None),
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    country_name = None
    uploaded_key = None
    logo_url = None

    if country_id:
        country = await _get_country(service.db, country_id)
        if country is None:
            return error("Invalid country id", 400, code="VALIDATION_ERROR")
        country_name = country.name

    if logo is not None and logo.filename:
        logo_url, uploaded_key = storage_service.upload_logo(
            logo.file,
            logo.filename or "logo",
            logo.content_type or "application/octet-stream",
            organization_id=str(org_id),
        )

    try:
        await service.update_organization(
            org_id,
            UUID(current_user["user_id"]),
            name=name,
            domain=domain,
            team_size=team_size,
            country=country_name,
            logo_url=logo_url,
        )
    except Exception:
        if uploaded_key:
            storage_service.delete_object(uploaded_key)
        raise
    return success("Updated Organization successfully", data={"organizationID": str(org_id)})


# ------------------------------------------------------------- get org


@router.get("/organization/get")
async def get_organization(
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    org = await service.get_organization_by_id(org_id, UUID(current_user["user_id"]))
    return success(
        "Organization detail received successfully",
        data=service.to_summary(org).model_dump(mode="json"),
    )


# ------------------------------------------------------- get all orgs (admin)


@router.get("/organization")
async def get_all_organizations(
    filter_: OrganizationFilterRequest = Query(),
    service: OrganizationService = Depends(_service),
    _: dict = Depends(get_current_user),
):
    summaries, pagination = await service.get_all_organizations(filter_)
    return success(
        "All organizations retrieved successfully",
        data=[s.model_dump(mode="json") for s in summaries],
        meta=pagination.model_dump(mode="json"),
    )


# ----------------------------------------------------- update org status


@router.patch("/organization/status/{organization_id}")
async def update_organization_status(
    organization_id: UUID,
    payload: UpdateOrganizationStatusRequest,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    await service.update_organization_status(organization_id, payload.is_active, UUID(current_user["user_id"]))
    message = "Organization activated successfully" if payload.is_active else "Organization deactivated successfully"
    return success(
        message,
        data={"organization_id": str(organization_id), "is_active": payload.is_active},
    )


# ----------------------------------------------------------- create org


@router.post("/organization/create", status_code=201)
async def create_organization(
    name: str = Form(...),
    domain: str = Form(...),
    industry: str = Form(...),
    team_size: str = Form(...),
    country_id: str = Form(...),
    logo: Optional[UploadFile] = File(None),
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]

    if industry not in {
        "Information_Technology", "Finance", "Healthcare", "Education",
        "Manufacturing", "Retail", "Real Estate", "Logistics", "Hospitality", "Other",
    }:
        return error("Invalid industry", 400, code="VALIDATION_ERROR")
    if team_size not in {"1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"}:
        return error("Invalid team_size", 400, code="VALIDATION_ERROR")

    country = await _get_country(service.db, country_id)
    if country is None:
        return error("Invalid country id", 400, code="VALIDATION_ERROR")

    logo_url = None
    uploaded_key = None
    organization_id = str(uuid4())
    if logo is not None and logo.filename:
        logo_url, uploaded_key = storage_service.upload_logo(
            logo.file,
            logo.filename or "logo",
            logo.content_type or "application/octet-stream",
            organization_id=organization_id,
        )

    try:
        tokens = await service.create_organization(
            name=name, domain=domain, industry=industry, team_size=team_size,
            country=country.name, created_by=user_id, logo_url=logo_url,
            organization_id=organization_id,
        )
    except Exception:
        if uploaded_key:
            storage_service.delete_object(uploaded_key)
        raise

    response = success(
        "Successfully Created",
        status_code=201,
    )
    secure = os.getenv("COOKIE_SECURE", "") in ("true", "1", "yes")
    response.set_cookie(
        "access_token", tokens.access_token, max_age=tokens.expires_in, httponly=True, secure=secure
    )
    response.set_cookie(
        "refresh_token", tokens.refresh_token, max_age=tokens.refresh_expires_in, httponly=True, secure=secure
    )
    return response


# ------------------------------------------------------ update user status


@router.patch("/organization/user-status")
async def update_user_status(
    payload: UserStatusRequest,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    await service.update_user_status(org_id, payload.user_id, payload.is_active)
    return success(
        "Updated User Status successfully",
        data={"OrganizationID": str(org_id), "user_id": str(payload.user_id)},
    )


# ------------------------------------------------------ update user role


@router.patch("/organization/user-role")
async def update_user_role(
    payload: UserRoleRequest,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    if payload.role not in ("org_admin", "member"):
        return error("Role must be one of org_admin or member.", 400, code="VALIDATION_ERROR")
    org_id = UUID(current_user["organization_id"])
    await service.update_user_role(org_id, payload.user_id, payload.role)
    return success(
        "Updated User Role successfully",
        data={"OrganizationID": str(org_id), "user_id": str(payload.user_id)},
    )


# ------------------------------------------------------ get org users


@router.get("/organization/get-users")
async def get_users_in_organization(
    filter_: OrganizationMemberListFilter = Query(),
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    users, pagination = await service.get_users_in_organization(org_id, filter_)
    return success(
        "Organization detail received successfully",
        data=[u.model_dump(mode="json") for u in users],
        meta=pagination.model_dump(mode="json"),
    )


# ------------------------------------------------------ get all members


@router.get("/organization/all-members")
async def get_all_members(
    filter_: GlobalMemberListFilter = Query(),
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    users, pagination = await service.get_all_members(filter_)
    return success(
        "All members retrieved successfully.",
        data=[u.model_dump(mode="json") for u in users],
        meta=pagination.model_dump(mode="json"),
    )


# ------------------------------------------------------------- remove user


@router.delete("/organization/remove-user/{user_id}")
async def remove_user(
    user_id: UUID,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    await service.remove_user(org_id, user_id)
    return success(
        "Removed User Successfully",
        data={"OrganizationID": str(org_id), "user_id": str(user_id)},
    )


# --------------------------------------------------------------- invite


@router.post("/organization/invite", status_code=201)
async def invite_member(
    payload: InviteOrganizationMemberRequest,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    user_id = UUID(current_user["user_id"])
    await service.invite_member(org_id, user_id, payload)
    return success("Invitation sent successfully", status_code=201)


# -------------------------------------------------- invitation accept page


@router.get("/organization/invitations/accept", response_class=HTMLResponse)
async def accept_invitation_page(
    request: Request,
    token: Optional[str] = Query(None),
    service: OrganizationService = Depends(_service),
):
    if not token:
        return error(
            "Invitation token is required",
            status_code=400,
            code="VALIDATION_ERROR",
        )
    base_url = os.getenv("FRONTEND_DASHBOARD_URL", "http://localhost:3000").rstrip("/")
    dashboard_url = f"{base_url}/dashboard"
    login_url = f"{base_url}/signin"

    invitation = await service.get_invitation_by_token(token)
    if invitation is None:
        return _invitation_page(
            "invitation_not_found.html", DashboardURL=dashboard_url
        )
    if invitation.status == "accepted":
        return _invitation_page(
            "invitation_already_accepted.html", DashboardURL=dashboard_url
        )
    if invitation.status == "expired" or invitation.expires_at < _now():
        return _invitation_page(
            "invitation_expired.html", DashboardURL=dashboard_url
        )

    if not request.cookies.get("access_token"):
        return _invitation_page(
            "invitation_login_required.html", LoginURL=login_url
        )

    return _invitation_page("accept_invitation.html", Token=token)


# ---------------------------------------------------------- accept invite


@router.post("/organization/invitations/accept")
async def accept_invitation(
    request: Request,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    base_url = os.getenv("FRONTEND_DASHBOARD_URL", "http://localhost:3000").rstrip("/")
    dashboard_url = f"{base_url}/dashboard"
    content_type = request.headers.get("content-type", "")
    is_form = "application/x-www-form-urlencoded" in content_type
    try:
        if is_form or "multipart/form-data" in content_type:
            payload = await request.form()
            token = payload.get("token")
        else:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            token = payload.get("token") if isinstance(payload, dict) else None
        await service.accept_invitation(UUID(user_id), token)
    except HTTPException as exc:
        if is_form:
            return _invitation_page(
                "invitation_error.html",
                Message=str(exc.detail),
                DashboardURL=dashboard_url,
            )
        raise
    if is_form:
        return RedirectResponse(f"{base_url}/teams", status_code=302)
    return success("Invitation accepted successfully")


# ------------------------------------------------------------------ roles
# Roles are custom (non-system) org-scoped roles.


@router.post("/organization/roles", status_code=201)
async def create_role(
    payload: CreateRoleRequest,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    role = await service.create_role(org_id, payload)
    return success(
        "Role created successfully",
        data=role.model_dump(mode="json"),
        status_code=201,
    )


@router.get("/organization/roles")
async def get_roles(
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    roles = await service.get_roles_by_organization_id(org_id)
    return success(
        "Roles fetched successfully",
        data=[r.model_dump(mode="json") for r in roles],
    )


@router.get("/organization/roles/{role_id}")
async def get_role(
    role_id: UUID,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    role = await service.get_role_by_id(org_id, role_id)
    return success(
        "Role fetched successfully",
        data=role.model_dump(mode="json"),
    )


@router.patch("/organization/roles/{role_id}")
async def update_role(
    role_id: UUID,
    payload: UpdateRoleRequest,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    role = await service.update_role(org_id, role_id, payload)
    return success(
        "Role updated successfully",
        data=role.model_dump(mode="json"),
    )


@router.delete("/organization/roles/{role_id}")
async def delete_role(
    role_id: UUID,
    service: OrganizationService = Depends(_service),
    current_user: dict = Depends(get_current_user),
):
    org_id = UUID(current_user["organization_id"])
    await service.delete_role(org_id, role_id)
    return success("Role deleted successfully", data=None)


def _html(body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!DOCTYPE html><html><body style='font-family:sans-serif;text-align:center;margin-top:60px'>{body}</body></html>"
    )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def _get_country(db: AsyncSession, country_id: str) -> Optional[Country]:
    result = await db.execute(select(Country).where(Country.id == country_id))
    return result.scalar_one_or_none()
