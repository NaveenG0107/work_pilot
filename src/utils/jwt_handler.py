# src/utils/jwt_handler.py
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException, status

from src.utils.setting import get_settings


def get_platform_lifetimes() -> dict[str, dict[str, Optional[timedelta]]]:
    """Dynamically get token lifetimes based on environment configuration."""
    settings = get_settings()
    return {
        "web": {
            "access_expires": timedelta(seconds=settings.jwt_expiry),
            "refresh_expires": timedelta(seconds=settings.refresh_token_expiry),
        },
        "mobile": {
            "access_expires": None,  # Mobile tokens omit exp claim
            "refresh_expires": None,  # Mobile tokens omit exp claim
        },
    }


# Backwards compatibility export
class _PlatformTokenLifetimesProxy(dict):
    def __getitem__(self, key):
        return get_platform_lifetimes()[key]

    def get(self, key, default=None):
        return get_platform_lifetimes().get(key, default)


PLATFORM_TOKEN_LIFETIMES = _PlatformTokenLifetimesProxy()


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    organization_id: Optional[str] = None,
    platform: str = "web",
) -> str:
    """Generate JWT access token.
    For web clients, sets an expiration timestamp ('exp') based on environment settings.
    For mobile clients, omits the 'exp' claim per mobile requirements.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "user_id": user_id,
        "email": email,
        "role": role,
        "organization_id": organization_id,
        "platform": platform,
        "type": "access",
        "jti": str(uuid4()),
        "iat": now,
    }

    if platform != "mobile":
        lifetimes = get_platform_lifetimes().get(platform, get_platform_lifetimes()["web"])
        lifetime = lifetimes.get("access_expires")
        if lifetime:
            payload["exp"] = now + lifetime

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    user_id: str,
    email: str,
    role: str,
    organization_id: Optional[str] = None,
    platform: str = "web",
) -> str:
    """Generate JWT refresh token.
    For web clients, sets an expiration timestamp ('exp') based on environment settings.
    For mobile clients, omits the 'exp' claim per mobile requirements.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "user_id": user_id,
        "email": email,
        "role": role,
        "organization_id": organization_id,
        "platform": platform,
        "type": "refresh",
        "jti": str(uuid4()),
        "iat": now,
    }

    if platform != "mobile":
        lifetimes = get_platform_lifetimes().get(platform, get_platform_lifetimes()["web"])
        lifetime = lifetimes.get("refresh_expires")
        if lifetime:
            payload["exp"] = now + lifetime

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT.
    PyJWT validates 'exp' if present in claims. For tokens without 'exp' (mobile),
    it validates signature and other claims without requiring 'exp'.
    """
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
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