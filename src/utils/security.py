"""Security utilities for WorkPilot backend.

Provides:
- Redis-backed JWT token blocklist / revocation
- User active / deletion status caching
- Redis-backed distributed rate limiter
"""

from __future__ import annotations

from functools import wraps
import hashlib
import logging
import os
import time
from typing import Any, Callable, Optional
import uuid

from fastapi import Depends, HTTPException, Request, status

from src.database import get_redis

logger = logging.getLogger(__name__)


def hash_token(token: str) -> str:
    """Return SHA-256 hex digest of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def blacklist_token(redis: Any, token: str, claims: Optional[dict[str, Any]] = None) -> None:
    """Add a JWT token to the Redis blacklist with a TTL matching its expiry."""
    if not redis or not token:
        return

    token_hash = hash_token(token)
    key = f"blacklist:token:{token_hash}"

    # Calculate TTL based on JWT exp claim if present, fallback to 30 days
    ttl = 86400 * 30
    if claims and isinstance(claims.get("exp"), (int, float)):
        now = int(time.time())
        exp = int(claims["exp"])
        ttl = max(1, exp - now)

    try:
        await redis.set(key, "revoked", ex=ttl)
        logger.info("Token blacklisted successfully (hash: %s..., ttl: %ds)", token_hash[:10], ttl)
    except Exception as exc:
        logger.warning("Failed to blacklist token in Redis: %s", exc)


async def is_token_blacklisted(redis: Any, token: str) -> bool:
    """Check if a JWT token has been revoked in Redis."""
    if not redis or not token:
        return False

    token_hash = hash_token(token)
    key = f"blacklist:token:{token_hash}"
    try:
        val = await redis.get(key)
        return val is not None
    except Exception as exc:
        logger.warning("Redis check failed for blacklisted token: %s", exc)
        return False


async def get_cached_user_status(redis: Any, user_id: str) -> Optional[str]:
    """Retrieve cached user active/inactive status from Redis."""
    if not redis or not user_id:
        return None
    try:
        return await redis.get(f"user:active:{user_id}")
    except Exception:
        return None


async def set_cached_user_status(redis: Any, user_id: str, is_active: bool, ttl: int = 60) -> None:
    """Cache user status in Redis (active / inactive)."""
    if not redis or not user_id:
        return
    status_str = "active" if is_active else "inactive"
    try:
        await redis.set(f"user:active:{user_id}", status_str, ex=ttl)
    except Exception:
        pass


async def invalidate_user_cache(redis: Any, user_id: str) -> None:
    """Invalidate cached status for a user."""
    if not redis or not user_id:
        return
    try:
        await redis.delete(f"user:active:{user_id}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------


async def check_rate_limit(
    redis: Any,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> bool:
    """Sliding/fixed window rate limit counter in Redis.

    Returns True if allowed, False if limit exceeded.
    Fails open on Redis connection issues so legitimate users are not blocked.
    """
    if not redis:
        return True

    try:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_seconds)
        return current <= max_requests
    except Exception as exc:
        logger.warning("Rate limit check failed (failing open): %s", exc)
        return True


def rate_limit(
    max_requests: int,
    window_seconds: int = 60,
    scope: str = "global",
) -> Callable:
    """FastAPI dependency for rate-limiting requests by IP or user identifier."""

    async def dependency(request: Request, redis: Any = Depends(get_redis)) -> None:
        # Check if rate limiting is disabled via env var for testing/benchmarks
        if os.getenv("DISABLE_RATE_LIMITING", "").lower() in ("true", "1", "yes"):
            return

        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
        user_id = getattr(request.state, "user_id", None) or client_ip
        key = f"ratelimit:{scope}:{user_id}"

        allowed = await check_rate_limit(redis, key, max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Please try again in {window_seconds} seconds.",
                headers={"Retry-After": str(window_seconds)},
            )

    return dependency
