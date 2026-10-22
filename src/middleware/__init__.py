"""
WorkPilot Middleware Package.
Centralizes all HTTP middleware: authentication, RBAC, session renewal, and cache invalidation.
"""

from src.middleware.auth import bearer_scheme, extract_token, get_current_user
from src.middleware.cache_invalidation import ProjectCacheInvalidationMiddleware
from src.middleware.rbac import has_default_permission, require_permission
from src.middleware.session import SlidingSessionMiddleware

__all__ = [
    "ProjectCacheInvalidationMiddleware",
    "SlidingSessionMiddleware",
    "bearer_scheme",
    "extract_token",
    "get_current_user",
    "has_default_permission",
    "require_permission",
]
