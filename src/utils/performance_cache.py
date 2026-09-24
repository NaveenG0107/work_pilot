"""Small, failure-tolerant Redis helpers for versioned read caches."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import asyncio
from typing import Any

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = max(1, int(os.getenv("PERFORMANCE_CACHE_TTL_SECONDS", "30")))
CACHE_TIMEOUT_SECONDS = max(
    0.05, float(os.getenv("PERFORMANCE_CACHE_TIMEOUT_SECONDS", "0.25"))
)
VERSION_TTL_SECONDS = 7 * 24 * 60 * 60


async def _bounded(awaitable):
    """Keep Redis strictly optional on request paths."""
    return await asyncio.wait_for(awaitable, timeout=CACHE_TIMEOUT_SECONDS)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


async def project_version(redis, project_id: str) -> int | None:
    if redis is None:
        return None
    try:
        raw = await _bounded(redis.get(f"wp:project-version:{project_id}"))
        return int(raw or 0)
    except Exception as exc:
        logger.warning("Redis cache version read failed: %s", exc)
        return None


async def cache_get_json(redis, key: str | None) -> Any | None:
    if redis is None or key is None:
        return None
    try:
        raw = await _bounded(redis.get(key))
        return json.loads(raw) if raw is not None else None
    except Exception as exc:
        logger.warning("Redis cache read failed for %s: %s", key, exc)
        return None


async def cache_set_json(redis, key: str | None, value: Any) -> None:
    if redis is None or key is None:
        return
    try:
        await _bounded(
            redis.set(
                key,
                json.dumps(value, separators=(",", ":"), default=str),
                ex=CACHE_TTL_SECONDS,
            )
        )
    except Exception as exc:
        logger.warning("Redis cache write failed for %s: %s", key, exc)


async def bump_project_version(redis, project_id: str | None) -> None:
    if redis is None or not project_id:
        return
    key = f"wp:project-version:{project_id}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, VERSION_TTL_SECONDS)
            await _bounded(pipe.execute())
    except Exception as exc:
        # Cache invalidation must never turn a committed mutation into an API error.
        logger.warning("Redis project cache invalidation failed for %s: %s", project_id, exc)
