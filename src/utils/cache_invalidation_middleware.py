"""Invalidate versioned project caches after successful HTTP mutations."""
from __future__ import annotations

import re

from src.database import get_redis
from src.utils.performance_cache import bump_project_version

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_PROJECT_PATHS = (
    re.compile(rf"^/api/v1/projects/(?P<id>{_UUID})(?:/|$)"),
    re.compile(rf"^/api/v1/project/(?P<id>{_UUID})(?:/|$)"),
    re.compile(rf"^/api/v1/(?P<id>{_UUID})/(?:labels|user-story-statuses)(?:/|$)"),
)


class ProjectCacheInvalidationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return

        project_id = None
        path = scope.get("path", "")
        for pattern in _PROJECT_PATHS:
            if match := pattern.match(path):
                project_id = match.group("id").lower()
                break

        status_code = 500

        async def capture_status(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, capture_status)
        if project_id and status_code < 400:
            await bump_project_version(get_redis(), project_id)
