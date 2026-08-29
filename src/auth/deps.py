from typing import Optional

from fastapi import Depends, HTTPException, Request

from src.utils.core import verify_jwt


def _extract_token(request: Request) -> Optional[str]:
    """Extract a JWT from the Authorization: Bearer header or the access_token cookie."""
    auth_header = request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()

    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token

    return None


async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency mirroring Go's ValidateJWT middleware.

    Populates and returns a dict with `user_id`, `role` and `organization_id`
    decoded from the access token. Raises 401/403 on missing/invalid tokens.
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=401, detail="Authentication required"
        )

    claims, err = verify_jwt(token)
    if err or not claims:
        raise HTTPException(
            status_code=401, detail="Authentication required"
        )

    user_id = claims.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to perform this action",
        )

    return {
        "user_id": user_id,
        "role": claims.get("role", ""),
        "organization_id": claims.get("organization_id"),
    }


def require_role(*allowed_roles: str):
    """
    Dependency factory mirroring Go's Authorize(rolesAllowed ...) middleware.

    Usage: `current_user: dict = Depends(require_role("admin", "member"))`
    """

    async def role_checker(
        current_user: dict = Depends(get_current_user),
    ) -> dict:
        role = current_user.get("role", "")
        if allowed_roles and role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return role_checker
