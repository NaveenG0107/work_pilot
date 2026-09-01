from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.utils.core import verify_jwt

security = HTTPBearer(auto_error=False)


def _extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    if credentials:
        return credentials.credentials

    return request.cookies.get("access_token")


async def get_current_user(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    token = _extract_token(request, credentials)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    claims, err = verify_jwt(token)
    if err or not claims:
        raise HTTPException(status_code=401, detail="Authentication required")

    # jwt_handler.create_access_token stores user_id under "sub" (JWT standard),
    # while core.create_jwt stores it under "user_id" (Go compat).  Accept both.
    user_id = claims.get("user_id") or claims.get("sub")
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
    async def role_checker(current_user: dict = Depends(get_current_user)) -> dict:
        role = current_user.get("role", "")
        if allowed_roles and role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return role_checker
