from typing import Optional, Any

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.database import get_db, get_redis
from src.utils.core import verify_jwt
from src.utils.security import (
    get_cached_user_status,
    is_token_blacklisted,
    set_cached_user_status,
)

security = HTTPBearer(auto_error=False)


def is_mobile_client(client_type: str | None = Header(default=None, alias="x-client-platform")) -> bool:
    return (client_type or "").strip().lower() == "mobile"


def _extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    if credentials:
        return credentials.credentials

    return request.cookies.get("access_token")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    redis: Any = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> dict:
    token = _extract_token(request, credentials)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    claims, err = verify_jwt(token)
    if err or not claims:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Check token revocation blocklist in Redis
    if await is_token_blacklisted(redis, token):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    # jwt_handler.create_access_token stores user_id under "sub" (JWT standard),
    # while core.create_jwt stores it under "user_id" (Go compat).  Accept both.
    user_id = claims.get("user_id") or claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to perform this action",
        )

    # Check user active/deleted status (cached in Redis for performance)
    cached_status = await get_cached_user_status(redis, str(user_id))
    if cached_status == "inactive":
        raise HTTPException(
            status_code=401,
            detail="User account is inactive or has been deleted",
        )
    elif cached_status is None:
        stmt = select(User.is_active, User.deleted_at).where(User.id == str(user_id))
        res = await db.execute(stmt)
        user_row = res.first()
        if not user_row or user_row.deleted_at is not None or not user_row.is_active:
            await set_cached_user_status(redis, str(user_id), False, ttl=60)
            raise HTTPException(
                status_code=401,
                detail="User account is inactive or has been deleted",
            )
        await set_cached_user_status(redis, str(user_id), True, ttl=60)

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
