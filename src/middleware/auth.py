# src/middleware/auth.py
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.utils.jwt_handler import decode_token

# HTTPBearer extracts the token from the Authorization header
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


def extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> str:
    """
    Extract JWT token from:
    1. Authorization: Bearer <token> header
    2. access_token cookie (fallback)
    
    Replaces getAuthToken in validate.go
    """
    # Prioritize Bearer header
    if credentials is not None:
        return credentials.credentials
    
    # Fallback to cookie
    token = request.cookies.get("access_token")
    if token:
        return token
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(request: Request, token: str = Depends(extract_token)) -> dict:
    """
    Validate JWT and extract user context.
    Replaces the middleware logic in validate.go.
    
    Returns dict with:
    - user_id: str
    - role: str
    - organization_id: Optional[str]
    """
    payload = decode_token(token)
    
    # Reject refresh tokens used as access tokens
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )
    
    request.state.user_id = payload.get("sub")
    request.state.role = payload.get("role")
    request.state.organization_id = payload.get("organization_id")
    
    return {
        "user_id": payload.get("sub"),
        "role": payload.get("role"),
        "organization_id": payload.get("organization_id"),
    }