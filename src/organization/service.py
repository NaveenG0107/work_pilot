# src/organization/service.py
"""
Organization service logic.

Mirrors internal/services/organization.go. DB access is done inline using the
synchronous SQLAlchemy Session (the FastAPI project uses sync sessions).
"""

import math
import os
import re
import secrets
import string
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.audit.models import AuditLog
from src.auth.models import RefreshToken, User
from src.config import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_REFRESH_TOKEN_EXPIRE_DAYS
from src.utils import email as email_service
from src.organization.models import (
    InvitationStatus,
    Organization,
    OrganizationInvitation,
    Permission,
    Role,
    RolePermission,
)
from src.organization.schemas import (
    AuthTokensResponse,
    CreateRoleRequest,
    GlobalMemberListFilter,
    InviteOrganizationMemberRequest,
    OrganizationFilterRequest,
    OrganizationMemberListFilter,
    OrganizationSummary,
    Pagination,
    RoleResponse,
    UpdateRoleRequest,
    UserProfile,
)
from src.project.models import Project, ProjectMember
from src.utils.jwt_handler import create_access_token
from src.utils.password_helper import hash_password

INDUSTRY_VALUES = {
    "Information_Technology",
    "Finance",
    "Healthcare",
    "Education",
    "Manufacturing",
    "Retail",
    "Real Estate",
    "Logistics",
    "Hospitality",
    "Other",
}

TEAM_SIZE_VALUES = {"1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"}

GLOBAL_ROLE = "developer"

# Default role permission filters mirroring CreateDefaultRolesForOrg in
# organization-repo/organization.go. A role starts with '' (all) or a subset.
DEFAULT_ROLE_SCOPE: dict = {
    "org_admin": None,  # all permissions
    "project_manager": None,  # all except projects add/delete is applied below
    "developer": {
        ("projects", "view"),
        ("sprints", "view"),
        ("user_stories", "view"),
        ("user_stories", "add"),
        ("user_stories", "modify"),
        ("tasks", "view"),
        ("tasks", "add"),
        ("tasks", "modify"),
        ("tasks", "delete"),
        ("comments", "view"),
        ("comments", "add"),
        ("comments", "modify"),
        ("comments", "delete"),
        ("comments", "comment"),
    },
    "qa": {
        ("projects", "view"),
        ("sprints", "view"),
        ("user_stories", "view"),
        ("user_stories", "modify"),
        ("tasks", "view"),
        ("tasks", "add"),
        ("tasks", "modify"),
        ("comments", "view"),
        ("comments", "add"),
        ("comments", "comment"),
    },
    "stakeholder": {
        ("projects", "view"),
        ("sprints", "view"),
        ("user_stories", "view"),
        ("tasks", "view"),
        ("comments", "view"),
        ("comments", "comment"),
    },
}

PROJECT_MANAGER_EXCLUDED = {("projects", "add"), ("projects", "delete")}

ROLE_DESCRIPTIONS = {
    "org_admin": "Organization administrator with full access to organization resources",
    "project_manager": "Project manager with access to manage projects, sprints, and team activities",
    "developer": "Software developer with access to view and modify user stories and tasks",
    "qa": "Quality assurance engineer with access to test tasks",
    "stakeholder": "Read-only stakeholder with basic viewing and commenting privileges",
}


def _slug_from_domain(domain: str) -> str:
    domain = (domain or "").strip()
    domain = domain.rstrip("/")
    return domain.split("/")[-1]


def _parse_org_duplicate(exc: IntegrityError) -> HTTPException:
    msg = (exc.orig and str(exc.orig) or "").lower()
    if "slug" in msg or "domain" in msg:
        detail = "An organization with this domain or slug already exists"
    elif "name" in msg:
        detail = "An organization with this name already exists"
    else:
        detail = "Organization already exists"
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _parse_user_duplicate(exc: IntegrityError) -> HTTPException:
    msg = (exc.orig and str(exc.orig) or "").lower()
    if "username" in msg:
        detail = "Username is already taken"
    elif "email" in msg:
        detail = "User with this email already exists"
    else:
        detail = "User with this email or username already exists"
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _page_meta(page: int, page_size: int, total_items: int) -> Pagination:
    total_pages = max(1, int(math.ceil(total_items / page_size)))
    return Pagination(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_previous=page > 1,
    )


def _normalize_page(page: int, page_size: int, default_page_size: int = 10) -> Tuple[int, int]:
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = default_page_size
    return page, page_size


def _fill_audit(db: Session, **kwargs) -> None:
    """Create an audit log row (mirrors s.auditRepo.CreateAuditLog)."""
    kwargs.setdefault("type", "audit")
    kwargs.setdefault("created_at", datetime.now(timezone.utc))
    log = AuditLog(id=str(uuid_lib.uuid4()), **kwargs)
    db.add(log)
    db.commit()


class OrganizationService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------------- get

    def get_organization_by_id(self, org_id: UUID, user_id: UUID) -> Organization:
        org = self.db.query(Organization).filter(Organization.id == str(org_id)).first()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        self.db.add(AuditLog(
            id=str(uuid_lib.uuid4()), user_id=str(user_id), organization_id=str(org_id),
            action="viewed", resource_type="organization", resource_id=str(org_id),
            type="audit", created_at=datetime.now(timezone.utc),
        ))
        self.db.commit()
        return org

    def to_summary(self, org: Organization, project_count: int = 0, member_count: int = 0) -> OrganizationSummary:
        return OrganizationSummary(
            id=org.id, name=org.name, slug=org.slug or None, domain=org.domain,
            industry=org.industry or None, team_size=org.team_size or None,
            country=org.country or None, logo_url=org.logo_url or None,
            is_active=org.is_active, created_at=org.created_at,
            total_projects=project_count, total_members=member_count,
        )

    def get_all_organizations(self, filter_: OrganizationFilterRequest) -> Tuple[List[OrganizationSummary], Pagination]:
        page, page_size = _normalize_page(filter_.page, filter_.page_size)
        query = self.db.query(Organization)

        if filter_.name:
            query = query.filter(func.lower(Organization.name).like(f"%{filter_.name.strip().lower()}%"))
        if filter_.domain:
            query = query.filter(func.lower(Organization.domain).like(f"%{filter_.domain.strip().lower()}%"))
        if filter_.industry:
            query = query.filter(func.lower(Organization.industry) == filter_.industry.strip().lower())
        if filter_.team_size:
            query = query.filter(Organization.team_size == filter_.team_size.strip())
        if filter_.country:
            query = query.filter(func.lower(Organization.country) == filter_.country.strip().lower())
        if filter_.is_active is not None:
            query = query.filter(Organization.is_active == filter_.is_active)
        if filter_.search:
            term = f"%{filter_.search.strip().lower()}%"
            query = query.filter(or_(
                func.lower(Organization.name).like(term),
                func.lower(Organization.domain).like(term),
                func.lower(Organization.slug).like(term),
                func.lower(Organization.industry).like(term),
            ))

        # sort (whitelist)
        allowed = {"name", "created_at", "updated_at", "domain", "industry", "team_size", "is_active"}
        sort_by = (filter_.sort_by or "created_at").strip()
        sort_order = filter_.sort_order.strip().upper()
        if sort_by not in allowed:
            sort_by = "created_at"
        if sort_order not in ("ASC", "DESC"):
            sort_order = "DESC"
        col = getattr(Organization, sort_by)
        query = query.order_by(col.asc() if sort_order == "ASC" else col.desc())

        total_items = query.count()
        rows = query.offset((page - 1) * page_size).limit(page_size).all()

        counts = self.get_project_member_counts([str(o.id) for o in rows])
        summaries = [
            self.to_summary(o, project_count=counts[o.id][0], member_count=counts[o.id][1])
            for o in rows
        ]
        return summaries, _page_meta(page, page_size, total_items)

    def get_project_member_counts(self, org_ids: List[str]) -> dict:
        counts: dict = {oid: (0, 0) for oid in org_ids}
        if not org_ids:
            return counts
        project_rows = (
            self.db.query(Project.organization_id, func.count(Project.id))
            .filter(Project.organization_id.in_(org_ids), Project.deleted_at.is_(None))
            .group_by(Project.organization_id)
            .all()
        )
        member_rows = (
            self.db.query(User.organization_id, func.count(User.id))
            .filter(User.organization_id.in_(org_ids), User.deleted_at.is_(None))
            .group_by(User.organization_id)
            .all()
        )
        for oid, cnt in project_rows:
            counts.setdefault(oid, (0, 0))
            p, m = counts[oid]
            counts[oid] = (cnt, m)
        for oid, cnt in member_rows:
            counts.setdefault(oid, (0, 0))
            p, m = counts[oid]
            counts[oid] = (p, cnt)
        return counts

    # ---------------------------------------------------------------- create

    def create_organization(self, name: str, domain: str, industry: str, team_size: str,
                            country: str, created_by: str, logo_url: Optional[str]) -> AuthTokensResponse:
        slug = _slug_from_domain(domain)
        org = Organization(
            id=str(uuid_lib.uuid4()), name=name, domain=domain, industry=industry,
            team_size=team_size, country=country, logo_url=logo_url or None,
            slug=slug, created_by=created_by, is_active=True,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        self.db.add(org)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            _parse_org_duplicate(exc)

        self.db.refresh(org)

        # Seed default roles for the new org
        self.create_default_roles_for_org(org.id)

        org_admin_role = self._get_role_by_name_and_org("org_admin", org.id)
        if org_admin_role is None:
            self._hard_delete_org(org.id)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

        user = self.db.query(User).filter(User.id == created_by).first()
        if user is None:
            self._hard_delete_org(org.id)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        user.organization_id = org.id
        user.role_id = org_admin_role.id
        user.is_active = True
        user.joined_at = datetime.now(timezone.utc)
        self.db.commit()

        tokens = self._issue_tokens(user, role_name="org_admin", org_id=org.id)

        _fill_audit(
            self.db, user_id=created_by, organization_id=org.id, action="created",
            resource_type="organization", resource_id=org.id, details="created",
        )
        return tokens

    def _issue_tokens(self, user: User, role_name: str, org_id: str, platform: str = "web") -> AuthTokensResponse:
        access_token = create_access_token(
            user_id=user.id, email=user.email, role=role_name,
            organization_id=org_id, platform=platform,
        )
        # Opaque refresh token (Go parity): hash a random value and store it.
        refresh_value = secrets.token_urlsafe(48)
        hashed = hash_password(refresh_value)
        expires_in = JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        refresh_expires_in = JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
        stored = RefreshToken(
            id=str(uuid_lib.uuid4()), user_id=user.id, token_hash=hashed,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=refresh_expires_in),
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        self.db.add(stored)
        self.db.commit()
        return AuthTokensResponse(
            access_token=access_token,
            refresh_token=f"{stored.id}.{refresh_value}",
            token_type="Bearer",
            expires_in=expires_in,
            refresh_expires_in=refresh_expires_in,
            require_password_change=bool(user.require_password_change),
        )

    def _hard_delete_org(self, org_id: str) -> None:
        self.db.query(Organization).filter(Organization.id == org_id).delete()
        self.db.commit()

    # ------------------------------------------------------------- roles

    def _get_role_by_name_and_org(self, name: str, org_id: str) -> Optional[Role]:
        return (
            self.db.query(Role)
            .filter(Role.name == name, Role.organization_id == org_id)
            .first()
        )

    def create_default_roles_for_org(self, org_id: str) -> None:
        perms = self.db.query(Permission).all()
        for name, scope in DEFAULT_ROLE_SCOPE.items():
            existing = self.db.query(Role).filter(
                Role.name == name, Role.organization_id == org_id
            ).first()
            if existing:
                continue
            role = Role(
                id=str(uuid_lib.uuid4()), organization_id=org_id, name=name,
                description=ROLE_DESCRIPTIONS[name], is_system=True,
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
            self.db.add(role)
            self.db.flush()

            selected = perms
            if name == "project_manager":
                selected = [p for p in perms if (p.resource, p.action) not in PROJECT_MANAGER_EXCLUDED]
            elif scope is not None:
                selected = [p for p in perms if (p.resource, p.action) in scope]

            for perm in selected:
                self.db.add(RolePermission(role_id=role.id, permission_id=perm.id))
        self.db.commit()

    # --------------------------------------------------------------- update

    def update_organization(self, org_id: UUID, name: Optional[str], domain: Optional[str],
                            team_size: Optional[str], country: Optional[str]) -> None:
        org = self.db.query(Organization).filter(Organization.id == str(org_id)).first()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        if domain is not None and domain.strip():
            org.slug = _slug_from_domain(domain)
        if name is not None:
            org.name = name
        if domain is not None and domain.strip():
            org.domain = domain
        if team_size is not None:
            org.team_size = team_size
        if country is not None:
            org.country = country
        org.updated_at = datetime.now(timezone.utc)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            _parse_org_duplicate(exc)
        _fill_audit(
            self.db, user_id=org_id, organization_id=org_id, action="updated",
            resource_type="organization", resource_id=org_id, details="updated",
        )

    def delete_organization(self, org_id: UUID, user_id: UUID) -> None:
        result = self.db.query(Organization).filter(Organization.id == str(org_id)).delete()
        if result == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        self.db.commit()
        _fill_audit(
            self.db, user_id=user_id, organization_id=org_id, action="deleted",
            resource_type="organization", resource_id=org_id, details="deleted",
        )

    def update_organization_status(self, org_id: UUID, is_active: bool, actor_id: UUID) -> None:
        org = self.db.query(Organization).filter(Organization.id == str(org_id)).first()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        org.is_active = is_active
        if is_active:
            org.deleted_at = None
        else:
            org.deleted_at = datetime.now(timezone.utc)
        org.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        status_str = "activated" if is_active else "deactivated"
        _fill_audit(
            self.db, user_id=actor_id, organization_id=org_id, action="updated",
            resource_type="organization_status", resource_id=org_id,
            details=f"organization {org.name} ({status_str})",
        )

    # ------------------------------------------------------------ user mgmt

    def update_user_status(self, org_id: UUID, user_id: UUID, is_active: bool) -> None:
        user = self.db.query(User).filter(User.id == str(user_id)).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user.organization_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action")
        if user.organization_id != str(org_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action")
        user.is_active = is_active
        user.status = "active" if is_active else "inactive"
        self.db.commit()
        _fill_audit(
            self.db, user_id=str(user_id), organization_id=str(org_id), action="updated",
            resource_type="user_status", resource_id=str(user_id),
            details=f"updated user status for {user.email}",
        )

    def update_user_role(self, org_id: UUID, user_id: UUID, role: str) -> None:
        user = self.db.query(User).filter(User.id == str(user_id)).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user.organization_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action")
        if user.organization_id != str(org_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action")
        role_name = "org_admin" if role == "org_admin" else GLOBAL_ROLE
        role_obj = self._get_role_by_name_and_org(role_name, str(org_id))
        if role_obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
        user.role_id = role_obj.id
        self.db.commit()
        _fill_audit(
            self.db, user_id=str(user_id), organization_id=str(org_id), action="updated",
            resource_type="user_role", resource_id=str(user_id),
            details=f"updated user role for {user.email}",
        )

    def remove_user(self, org_id: UUID, user_id: UUID) -> None:
        user = self.db.query(User).filter(User.id == str(user_id)).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user.role is not None and user.role.name == "org_admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized access: cannot remove organization admin")
        if user.organization_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action")
        if user.organization_id != str(org_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action")
        # Detach from the organization instead of deleting (Go parity).
        user.organization_id = None
        user.role_id = "00000000-0000-0000-0000-000000000000"
        user.is_active = False
        user.joined_at = None
        self.db.commit()
        _fill_audit(
            self.db, user_id=str(user_id), organization_id=str(org_id), action="removed",
            resource_type="organization_user", resource_id=str(user_id),
            details="Removed user from organization",
        )

    # -------------------------------------------------------------- members

    def get_users_in_organization(self, org_id: UUID, filter_: OrganizationMemberListFilter) -> Tuple[List[UserProfile], Pagination]:
        page, page_size = _normalize_page(filter_.page, filter_.page_size)
        now = datetime.now(timezone.utc)

        # expire pending invitations and pending users
        self.db.query(OrganizationInvitation).filter(
            OrganizationInvitation.organization_id == str(org_id),
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at < now,
        ).update({"status": InvitationStatus.EXPIRED}, synchronize_session=False)
        expired_emails = [
            r[0] for r in self.db.query(OrganizationInvitation.email).filter(
                OrganizationInvitation.organization_id == str(org_id),
                OrganizationInvitation.status == InvitationStatus.EXPIRED,
            ).all()
        ]
        if expired_emails:
            self.db.query(User).filter(
                User.organization_id == str(org_id),
                User.status == "pending",
                User.email.in_(expired_emails),
            ).update({"status": "expired"}, synchronize_session=False)
        self.db.commit()

        query = (
            self.db.query(User)
            .outerjoin(Role, (Role.id == User.role_id) & (Role.deleted_at.is_(None)))
            .filter(User.organization_id == str(org_id))
        )
        if filter_.full_name:
            query = query.filter(User.full_name.ilike(f"%{filter_.full_name.strip()}%"))
        if filter_.email:
            query = query.filter(User.email.ilike(f"%{filter_.email.strip()}%"))
        if filter_.username:
            query = query.filter(User.username.ilike(f"%{filter_.username.strip()}%"))
        if filter_.role:
            query = query.filter(func.lower(Role.name) == filter_.role.strip().lower())
        if filter_.is_active is not None:
            query = query.filter(User.is_active == filter_.is_active)
        if filter_.is_verified is not None:
            query = query.filter(User.is_verified == filter_.is_verified)
        if filter_.status:
            query = query.filter(User.status == filter_.status.strip().lower())
        if filter_.timezone:
            query = query.filter(User.timezone.ilike(f"%{filter_.timezone.strip()}%"))
        if not filter_.include_org_admins:
            query = query.filter((func.lower(Role.name) != "org_admin") | (Role.name.is_(None)))

        total_items = query.count()
        users = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

        _fill_audit(
            self.db, organization_id=str(org_id), action="viewed",
            resource_type="users_in_organization", resource_id=str(org_id),
            details="view users in organization",
        )
        return [self._to_profile(u) for u in users], _page_meta(page, page_size, total_items)

    def get_all_members(self, filter_: GlobalMemberListFilter) -> Tuple[List[UserProfile], Pagination]:
        page, page_size = _normalize_page(filter_.page, filter_.page_size)
        query = (
            self.db.query(User)
            .outerjoin(Role, (Role.id == User.role_id) & (Role.deleted_at.is_(None)))
        )
        if filter_.organization_id is not None:
            query = query.filter(User.organization_id == str(filter_.organization_id))
        if filter_.search:
            term = f"%{filter_.search.strip().lower()}%"
            query = query.filter(or_(
                func.lower(User.full_name).like(term),
                func.lower(User.email).like(term),
                func.lower(User.username).like(term),
            ))
        if filter_.full_name:
            query = query.filter(func.lower(User.full_name).like(f"%{filter_.full_name.strip().lower()}%"))
        if filter_.email:
            query = query.filter(func.lower(User.email).like(f"%{filter_.email.strip().lower()}%"))
        if filter_.username:
            query = query.filter(func.lower(User.username).like(f"%{filter_.username.strip().lower()}%"))
        if filter_.role:
            query = query.filter(func.lower(Role.name) == filter_.role.strip().lower())
        if filter_.is_active is not None:
            query = query.filter(User.is_active == filter_.is_active)
        if filter_.is_verified is not None:
            query = query.filter(User.is_verified == filter_.is_verified)
        if filter_.timezone:
            query = query.filter(func.lower(User.timezone).like(f"%{filter_.timezone.strip().lower()}%"))

        allowed = {"full_name", "name", "email", "username", "role", "created_at", "joined_at", "is_active"}
        sort_by = (filter_.sort_by or "created_at").strip()
        sort_order = filter_.sort_order.strip().upper()
        if sort_by in ("full_name", "name"):
            sort_by = "full_name"
        elif sort_by not in allowed:
            sort_by = "created_at"
        if sort_order not in ("ASC", "DESC"):
            sort_order = "DESC"
        if sort_by == "role":
            query = query.order_by(Role.name.asc() if sort_order == "ASC" else Role.name.desc())
            sort_by = None
        col = getattr(User, sort_by) if sort_by else None

        total_items = query.count()
        if col is not None:
            query = query.order_by(col.asc() if sort_order == "ASC" else col.desc())
        users = query.offset((page - 1) * page_size).limit(page_size).all()

        _fill_audit(
            self.db, action="viewed", resource_type="all_users",
            type="audit", details="Super Admin viewed all system users/members",
            created_at=datetime.now(timezone.utc),
        )
        return [self._to_profile(u) for u in users], _page_meta(page, page_size, total_items)

    def _to_profile(self, user: User) -> UserProfile:
        return UserProfile(
            id=user.id, organization_id=user.organization_id,
            organization_name=(user.organization.name if user.organization else None),
            name=user.full_name, username=user.username, email=user.email,
            role=(user.role.name if user.role else None),
            avatar_url=user.avatar_url or None, color=user.color,
            timezone=user.timezone or None, is_active=user.is_active,
            is_verified=user.is_verified, status=user.status,
            created_at=user.created_at, joined_at=user.joined_at,
            require_password_change=user.require_password_change,
        )

    # ------------------------------------------------------------ invitations

    def invite_member(self, org_id: UUID, inviter_id: UUID, payload: InviteOrganizationMemberRequest) -> None:
        inviter = self.db.query(User).filter(User.id == str(inviter_id)).first()
        if (inviter is None or inviter.role is None or inviter.role.name != "org_admin"
                or inviter.organization_id is None or inviter.organization_id != str(org_id)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action")

        if not payload.members:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one member invitation is required")

        developer_role = self._get_role_by_name_and_org(GLOBAL_ROLE, str(org_id))
        if developer_role is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
        org = self.db.query(Organization).filter(Organization.id == str(org_id)).first()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

        for item in payload.members:
            invite_email = item.email.strip().lower()
            existing_user = self.db.query(User).filter(User.email == invite_email).first()

            user_existed = existing_user is not None
            if existing_user is not None:
                if existing_user.organization_id is not None:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already in an organization")
                existing_user.organization_id = str(org_id)
                existing_user.role_id = developer_role.id
                existing_user.is_active = False
                existing_user.status = "pending"
                self.db.commit()

            existing_pending = self.db.query(OrganizationInvitation).filter(
                OrganizationInvitation.organization_id == str(org_id),
                OrganizationInvitation.email == invite_email,
                OrganizationInvitation.status == InvitationStatus.PENDING,
            ).order_by(OrganizationInvitation.created_at.desc()).first()

            expires_at = datetime.now(timezone.utc) + timedelta(days=1)
            token = str(uuid_lib.uuid4())
            if existing_pending is not None:
                invitation = existing_pending
                invitation.token = token
                invitation.status = InvitationStatus.PENDING
                invitation.expires_at = expires_at
                invitation.accepted_at = None
                invitation.updated_at = datetime.now(timezone.utc)
            else:
                invitation = OrganizationInvitation(
                    id=str(uuid_lib.uuid4()), organization_id=str(org_id), email=invite_email,
                    role_id=developer_role.id, token=token, status=InvitationStatus.PENDING,
                    expires_at=expires_at, created_by=str(inviter_id),
                    created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
                )
                self.db.add(invitation)
            self.db.commit()

            invite_link = f"{os.getenv('BACKEND_API_URL', 'http://localhost:6369')}/api/v1/organization/invitations/accept?token={token}"
            temp_password = ""
            if not user_existed:
                temp_password = self._create_temp_user(invite_email, str(org_id), developer_role.id)

            email_service.send_organization_invitation(
                invite_email, org.name, developer_role.name, invite_link, temp_password,
            )

            _fill_audit(
                self.db, user_id=str(inviter_id), organization_id=str(org_id),
                action="invitation_sended", resource_type="organization_invitation",
                resource_id=token, details=f"Invited {invite_email} to {org.name}",
            )

    def _create_temp_user(self, email: str, org_id: str, role_id: str) -> str:
        temp_password = self._generate_temp_password(12)
        password_hash = hash_password(temp_password)
        username = self._generate_unique_username(email)
        user = User(
            id=str(uuid_lib.uuid4()), email=email,
            full_name=self._full_name_from_email(email), username=username,
            password_hash=password_hash, timezone="UTC", is_active=False,
            is_verified=True, organization_id=org_id, role_id=role_id,
            status="pending", created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc), require_password_change=True,
        )
        self.db.add(user)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            _parse_user_duplicate(exc)
        _fill_audit(
            self.db, organization_id=org_id, action="created",
            resource_type="temp_user", resource_id=user.id, details="create temp user",
        )
        return temp_password

    def _generate_temp_password(self, length: int) -> str:
        if length < 8:
            length = 8
        chars = string.ascii_letters + string.digits
        return "".join(secrets.choice(chars) for _ in range(length))

    def _generate_unique_username(self, email: str) -> str:
        base = self._username_from_email(email)
        for attempt in range(5):
            candidate = base if attempt == 0 else f"{base}{attempt}"
            exists = self.db.query(User).filter(User.username == candidate).first() is not None
            if not exists:
                return candidate
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unable to generate unique username for invited user")

    @staticmethod
    def _username_from_email(email: str) -> str:
        local = email.split("@")[0].strip().lower()
        parts = re.findall(r"[a-z0-9]+", local)
        if not parts:
            return "user"
        username = "_".join(parts)
        return username[:30]

    @staticmethod
    def _full_name_from_email(email: str) -> str:
        local = email.split("@")[0].strip().lower()
        parts = [p for p in re.findall(r"[a-z0-9]+", local) if p]
        if not parts:
            return "User"
        words = [p[0].upper() + p[1:].lower() for p in parts]
        return " ".join(words)

    def accept_invitation(self, user_id: UUID, token: str) -> None:
        if not token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation token is required")
        invitation = self.db.query(OrganizationInvitation).filter(
            OrganizationInvitation.token == token
        ).first()
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        if invitation.status == InvitationStatus.ACCEPTED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation has already been accepted")
        if invitation.status == InvitationStatus.EXPIRED or invitation.expires_at < datetime.now(timezone.utc):
            invitation.status = InvitationStatus.EXPIRED
            invitation.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation has expired")

        user = self.db.query(User).filter(User.id == str(user_id)).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user.email.lower() != invitation.email.lower():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to accept this invitation")
        if user.organization_id is not None and user.organization_id != invitation.organization_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already assigned to another organization")

        user.organization_id = invitation.organization_id
        user.role_id = invitation.role_id
        user.is_active = True
        user.status = "active"
        user.joined_at = datetime.now(timezone.utc)
        self.db.commit()

        accepted_at = datetime.now(timezone.utc)
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = accepted_at
        invitation.updated_at = accepted_at
        self.db.commit()

        _fill_audit(
            self.db, user_id=str(user_id), organization_id=invitation.organization_id,
            action="accepted", resource_type="invitation", resource_id=invitation.token,
            details="accepted invitation",
        )

    def get_invitation_by_token(self, token: str) -> OrganizationInvitation:
        invitation = self.db.query(OrganizationInvitation).filter(
            OrganizationInvitation.token == token
        ).first()
        _fill_audit(
            self.db, organization_id=(invitation.organization_id if invitation else None),
            action="viewed", resource_type="invitation",
            resource_id=(invitation.token if invitation else token),
            details="view invitation",
        )
        return invitation

    # ----------------------------------------------------------------- roles
    #
    # Mirrors internal/services/role.go + internal/handlers/http/role.go.
    # These are custom (non-system) roles scoped to an organization. The five
    # role endpoints live under /organization/roles in Go.

    @staticmethod
    def _role_permission_map(role: Role) -> Dict[str, Dict[str, bool]]:
        """Mirrors responsedto.MapToRoleResponse in Go."""
        resource_actions = {
            "projects": ["view", "add", "modify", "delete"],
            "sprints": ["view", "add", "modify", "delete"],
            "user_stories": ["view", "add", "modify", "delete"],
            "tasks": ["view", "add", "modify", "delete"],
            "comments": ["view", "add", "modify", "delete", "comment"],
        }
        permissions_map: Dict[str, Dict[str, bool]] = {}
        for res, actions in resource_actions.items():
            permissions_map[res] = {act: False for act in actions}
        for perm in role.permissions:
            if perm.resource in permissions_map and perm.action in permissions_map[perm.resource]:
                permissions_map[perm.resource][perm.action] = True
        return permissions_map

    def _map_role_response(self, role: Role) -> RoleResponse:
        return RoleResponse(
            id=UUID(role.id),
            organization_id=UUID(role.organization_id) if role.organization_id else None,
            name=role.name,
            description=role.description,
            is_system=bool(role.is_system),
            permissions=self._role_permission_map(role),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )

    def _get_role_by_id_raw(self, role_id: UUID) -> Role:
        role = self.db.query(Role).filter(Role.id == str(role_id)).first()
        if role is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
        return role

    def _get_permission_by_resource_action(self, resource: str, action: str) -> Permission:
        perm = (
            self.db.query(Permission)
            .filter(Permission.resource == resource, Permission.action == action)
            .first()
        )
        if perm is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permission: {action} for resource {resource}",
            )
        return perm

    def _resolve_enabled_permissions(self, permissions: Dict[str, Dict[str, bool]]) -> List[Permission]:
        resolved: List[Permission] = []
        for resource, action_map in permissions.items():
            for action, enabled in action_map.items():
                if enabled:
                    resolved.append(self._get_permission_by_resource_action(resource, action))
        return resolved

    def _is_role_assigned(self, role_id: UUID) -> bool:
        role_id_str = str(role_id)
        user = self.db.query(User).filter(User.role_id == role_id_str).first()
        if user is not None:
            return True
        member = self.db.query(ProjectMember).filter(ProjectMember.role_id == role_id_str).first()
        if member is not None:
            return True
        invite = self.db.query(OrganizationInvitation).filter(OrganizationInvitation.role_id == role_id_str).first()
        if invite is not None:
            return True
        return False

    def create_role(self, org_id: UUID, req: CreateRoleRequest) -> RoleResponse:
        name = req.name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role name is required")
        permissions_to_attach = self._resolve_enabled_permissions(req.permissions)

        role = Role(
            id=str(uuid_lib.uuid4()),
            organization_id=str(org_id),
            name=name,
            description=req.description,
            is_system=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        role.permissions = permissions_to_attach
        self.db.add(role)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A role with this name already exists in the organization",
            ) from exc
        saved = self._get_role_by_id_raw(UUID(role.id))
        return self._map_role_response(saved)

    def get_roles_by_organization_id(self, org_id: UUID) -> List[RoleResponse]:
        rows = (
            self.db.query(Role)
            .filter(
                (Role.organization_id == str(org_id))
                | (Role.organization_id.is_(None) & (Role.is_system.is_(True)))
            )
            .all()
        )
        return [self._map_role_response(r) for r in rows]

    def get_role_by_id(self, org_id: UUID, role_id: UUID) -> RoleResponse:
        role = self._get_role_by_id_raw(role_id)
        if role.organization_id is not None and role.organization_id != str(org_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this role")
        return self._map_role_response(role)

    def update_role(self, org_id: UUID, role_id: UUID, req: UpdateRoleRequest) -> RoleResponse:
        role = self._get_role_by_id_raw(role_id)
        if role.organization_id is None or role.organization_id != str(org_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to modify this role")

        if req.name is not None:
            name = req.name.strip()
            if not name:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role name cannot be empty")
            role.name = name
        if req.description is not None:
            role.description = req.description

        if req.permissions is not None:
            permissions_to_attach = self._resolve_enabled_permissions(req.permissions)
            role.permissions = permissions_to_attach
        role.updated_at = datetime.now(timezone.utc)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A role with this name already exists in the organization",
            ) from exc
        updated = self._get_role_by_id_raw(role_id)
        return self._map_role_response(updated)

    def delete_role(self, org_id: UUID, role_id: UUID) -> None:
        role = self._get_role_by_id_raw(role_id)
        if role.is_system:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="System roles cannot be deleted")
        if role.organization_id is None or role.organization_id != str(org_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this role")
        assigned = self._is_role_assigned(role_id)
        if assigned:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Role cannot be deleted because it is currently assigned to users, project members, or invitations",
            )
        self.db.query(Role).filter(Role.id == str(role_id)).delete()
        self.db.commit()
