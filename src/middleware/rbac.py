# src/middleware/rbac.py
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.auth.models import User
from src.auth.deps import get_current_user
from src.database import get_db
from src.organization.models import Role
from src.project.models import ProjectMember


# DEFAULT_ROLE_PERMISSIONS mirrors defaultRolePermissions from authorization.go
DEFAULT_ROLE_PERMISSIONS = {
    "org_admin": [
        "projects:view", "projects:add", "projects:modify", "projects:delete",
        "sprints:view", "sprints:add", "sprints:modify", "sprints:delete",
        "user_stories:view", "user_stories:add", "user_stories:modify", "user_stories:delete",
        "tasks:view", "tasks:add", "tasks:modify", "tasks:delete",
        "comments:view", "comments:add", "comments:modify", "comments:delete",
        "attachments:view", "attachments:add", "attachments:delete",
        "custom_statuses:view", "custom_statuses:modify",
    ],
    "project_manager": [
        "projects:view", "projects:modify",
        "sprints:view", "sprints:add", "sprints:modify", "sprints:delete",
        "user_stories:view", "user_stories:add", "user_stories:modify", "user_stories:delete",
        "tasks:view", "tasks:add", "tasks:modify", "tasks:delete",
        "comments:view", "comments:add", "comments:modify", "comments:delete",
        "attachments:view", "attachments:add", "attachments:delete",
        "custom_statuses:view", "custom_statuses:modify",
    ],
    "developer": [
        "projects:view",
        "sprints:view",
        "user_stories:view", "user_stories:add", "user_stories:modify",
        "tasks:view", "tasks:add", "tasks:modify", "tasks:delete",
        "comments:view", "comments:add", "comments:modify", "comments:delete",
        "attachments:view", "attachments:add", "attachments:delete",
        "custom_statuses:view",
    ],
    "qa": [
        "projects:view",
        "sprints:view",
        "user_stories:view", "user_stories:modify",
        "tasks:view", "tasks:add", "tasks:modify",
        "comments:view", "comments:add",
        "attachments:view", "attachments:add",
        "custom_statuses:view",
    ],
    "stakeholder": [
        "projects:view",
        "sprints:view",
        "user_stories:view",
        "tasks:view",
        "comments:view", "comments:add",
        "attachments:view",
        "custom_statuses:view",
    ],
}


def _normalize_role_name(role_name: str) -> Optional[str]:
    """Replaces the name-mapping logic in hasDefaultPermission (authorization.go)"""
    name = role_name.lower()
    mapping = {
        "member": "developer",
        "user": "developer",
        "tester": "qa",
        "viewer": "stakeholder",
    }
    return mapping.get(name, name)


def has_default_permission(role_name: str, resource: str, action: str) -> bool:
    """Replaces hasDefaultPermission in authorization.go"""
    normalized = _normalize_role_name(role_name)
    permissions = DEFAULT_ROLE_PERMISSIONS.get(normalized, [])
    target = f"{resource}:{action}"
    return target in permissions


def _role_has_permission(role: Role, resource: str, action: str) -> bool:
    for perm in role.permissions or []:
        if perm.resource == resource and perm.action == action:
            return True
    return has_default_permission(role.name, resource, action)


def require_permission(resource: str, action: str):
    """
    Replaces the RBAC middleware in authorize.go + CheckPermission in authorization.go.

    Usage:
        @router.get("/...")
        def get_project(
            _ = Depends(require_permission("projects", "view")),
        ):
            ...
    """
    def checker(
        request: Request,
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        user_id_str = current_user["user_id"]
        role_name = current_user["role"]

        # 1. Super admins are platform-level only, cannot do org/project activities
        if role_name == "super_admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

        # Load user with role from DB to verify (avoid trusting JWT claims blindly)
        user = db.query(User).filter_by(id=user_id_str).first()
        if user is None or user.role is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

        # 2. Project-level role check
        project_id = request.path_params.get("project_id")
        project_uuid = UUID(project_id) if project_id else None

        if project_uuid:
            member = (
                db.query(ProjectMember)
                .filter_by(project_id=str(project_uuid), user_id=user_id_str)
                .first()
            )
            if member and member.role is not None:
                if _role_has_permission(member.role, resource, action):
                    return current_user

        # 3. Organization-level role check
        org_id = current_user.get("organization_id")
        if user.organization_id is not None and org_id and str(user.organization_id) == org_id:
            if _role_has_permission(user.role, resource, action):
                return current_user
            # If the org role lacks the permission but the user is a project member
            # with the permission, allow it (only checked when org role disagrees).
            if project_uuid:
                member = (
                    db.query(ProjectMember)
                    .filter_by(project_id=str(project_uuid), user_id=user_id_str)
                    .first()
                )
                if member and member.role is not None and _role_has_permission(member.role, resource, action):
                    return current_user

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission")

    return checker
