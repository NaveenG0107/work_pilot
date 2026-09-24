"""Renew authenticated browser sessions without changing database state."""
import time

import jwt
from starlette.middleware.base import BaseHTTPMiddleware

from src.utils.core import set_access_token_cookie, verify_jwt
from src.utils.setting import get_settings


class SlidingSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        settings = get_settings()
        if not settings.jwt_sliding_session_enabled or response.status_code >= 400:
            return response
        # Authentication endpoints own token rotation, account switching, and logout.
        if (request.url.path.startswith("/api/v1/auth/")
                and request.url.path not in ("/api/v1/auth/me", "/api/v1/auth/me/insights", "/api/v1/auth/validate")):
            return response
        if request.method == "OPTIONS" or request.headers.get("authorization"):
            return response
        if any(header.lstrip().lower().startswith(("access_token=", "refresh_token="))
               for header in response.headers.getlist("set-cookie")):
            return response

        token = request.cookies.get("access_token")
        if not token or not getattr(request.state, "user_id", None):
            return response
        # Verify again after the handler: never revive an expired/invalid token.
        claims, error = verify_jwt(token)
        if error or not claims or str(claims.get("user_id")) != request.state.user_id:
            return response
        issued, expires = claims.get("iat"), claims.get("exp")
        if not isinstance(issued, (int, float)) or not isinstance(expires, (int, float)):
            return response  # Preserve legacy mobile tokens without expiry.
        now = int(time.time())
        ttl = settings.jwt_expiry
        interval = min(settings.jwt_renewal_interval, max(1, ttl // 2))
        if now - issued < interval or expires - now > ttl - interval:
            return response

        claims = {**claims, "iat": now, "exp": now + ttl}
        renewed = jwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")
        set_access_token_cookie(response, renewed, ttl, settings.cookie_secure)
        response.headers["Cache-Control"] = "no-store"
        return response
