# src/utils/jwt_handler.py
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException, status

from src.config import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, JWT_REFRESH_TOKEN_EXPIRE_DAYS, JWT_SECRET_KEY


# Platform-specific token lifetimes (mirrors generate.go)
PLATFORM_TOKEN_LIFETIMES = {
    "web": {
        "access_expires": timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        "refresh_expires": timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS),
    },
    "mobile": {
        "access_expires": timedelta(days=7),
        "refresh_expires": timedelta(days=30),
    },
}


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    organization_id: Optional[str] = None,
    platform: str = "web",
) -> str:
    """Replaces GenerateAccessToken in generate.go"""
    now = datetime.now(timezone.utc)
    expire = now + PLATFORM_TOKEN_LIFETIMES.get(platform, PLATFORM_TOKEN_LIFETIMES["web"])["access_expires"]

    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "organization_id": organization_id,
        "platform": platform,
        "type": "access",
        "jti": str(uuid4()),
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(
    user_id: str,
    email: str,
    role: str,
    organization_id: Optional[str] = None,
    platform: str = "web",
) -> str:
    """Replaces GenerateRefreshToken in generate.go"""
    now = datetime.now(timezone.utc)
    expire = now + PLATFORM_TOKEN_LIFETIMES.get(platform, PLATFORM_TOKEN_LIFETIMES["web"])["refresh_expires"]

    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "organization_id": organization_id,
        "platform": platform,
        "type": "refresh",
        "jti": str(uuid4()),
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT (replaces ValidateToken in validate.go)"""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )