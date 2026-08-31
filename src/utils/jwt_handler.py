# src/utils/jwt_handler.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

import jwt  # PyJWT
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.utils.setting import get_settings


# ---------------------------------------------------------------------------
# JWT creation / verification (HS256, platform-aware expiry)
# ---------------------------------------------------------------------------

def create_jwt(
    role: str,
    user_id: str,
    organization_id: Optional[str] = None,
    platform: str = "web",
    secret: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Create a JWT access token.
    Returns (token_string, error_dict_or_None).
    - web: expires in JWT_EXPIRY seconds (default 900 = 15 min)
    - mobile: no exp claim (ttl=0)
    """
    if secret is None:
        secret = get_settings().jwt_secret_key

    now = datetime.now(timezone.utc)
    claims = {
        "role": role,
        "user_id": user_id,
        "iat": now,
    }

    if organization_id is not None:
        claims["organization_id"] = organization_id

    # Platform expiry logic (mirrors Go JWT_EXPIRY, in seconds).
    expires_in_seconds = int(get_settings().jwt_expiry or 900)
    if platform != "mobile":
        # web clients get an expiry
        if expires_in_seconds > 0:
            claims["exp"] = now + timedelta(seconds=expires_in_seconds)

    token = jwt.encode(claims, secret, algorithm="HS256")
    return token, None


def verify_jwt(token: str, secret: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Verify a JWT access token.
    Returns (claims_dict_or_None, error_dict_or_None).
    """
    if secret is None:
        secret = get_settings().jwt_secret_key

    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
        return claims, None
    except jwt.ExpiredSignatureError:
        return None, {"error": "Token expired", "code": "TOKEN_EXPIRED"}
    except jwt.InvalidTokenError as e:
        return None, {"error": str(e), "code": "INVALID_TOKEN"}


# ---------------------------------------------------------------------------
# Bearer scheme + token extraction (mirrors Go transport/http middleware)
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Access token obtained from the login endpoint",
)


def _extract_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    """Return the bearer token from the header (or ``access_token`` cookie)."""
    token: str | None = None

    if isinstance(credentials, HTTPAuthorizationCredentials) and credentials.credentials:
        token = credentials.credentials.strip()
    else:
        authorization = request.headers.get("Authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            token = value.strip()
        elif request.cookies.get("access_token"):
            token = request.cookies["access_token"]

    return token or None


def _set_auth_state(request: Request, claims: Dict[str, Any]) -> None:
    """Populate request.state from verified JWT claims."""
    user_id = claims.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )

    request.state.user_id = str(user_id)
    request.state.organization_id = (
        str(claims["organization_id"])
        if claims.get("organization_id") is not None
        else None
    )
    request.state.role = str(claims.get("role") or "")
    request.state.claims = claims


# ---------------------------------------------------------------------------
# require_jwt — works BOTH as a FastAPI dependency and as a @decorator
# ---------------------------------------------------------------------------

def require_jwt(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Verify the request JWT and expose the claims via ``request.state``.

    Two usage modes (Go-style parity):

    1. Dependency mode (router/route level)::

        router = APIRouter(dependencies=[Depends(require_jwt)])
        current = Depends(require_jwt)

    2. Decorator mode on an endpoint that accepts ``request: Request``::

        @router.get("/...")
        @require_jwt
        async def handler(request: Request, ...):
            ...
    """
    if callable(request):
        # Decorator mode: ``request`` is actually the endpoint function.
        return _require_jwt_decorator(request)

    # Dependency mode: verify the JWT and populate request.state.
    token = _extract_bearer_token(request, credentials)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims, error = verify_jwt(token)

    if error or claims is None:
        message = (
            "Token expired"
            if error and error.get("code") == "TOKEN_EXPIRED"
            else "Invalid token"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=message,
            headers={"WWW-Authenticate": "Bearer"},
        )

    _set_auth_state(request, claims)
    return claims


def _require_jwt_decorator(endpoint: Callable) -> Callable:
    """Wrap an endpoint so it verifies the JWT before running."""

    @wraps(endpoint)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request")
        if request is None or not isinstance(request, Request):
            request = next(
                (arg for arg in args if isinstance(arg, Request)),
                None,
            )

        if request is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": "UNAUTHORIZED",
                    "status_code": 401,
                    "message": "Authentication required",
                },
            )

        auth_error = await authenticate_request(request)
        if auth_error is not None:
            return auth_error

        return await endpoint(*args, **kwargs)

    return _wrapper


# ---------------------------------------------------------------------------
# authenticate_request — Go-style middleware helper
# ---------------------------------------------------------------------------

async def authenticate_request(request: Request) -> Optional[JSONResponse]:
    """Verify the request JWT.

    Returns a JSON error response on failure (401 Unauthorized) or ``None``
    when the token is valid (with ``request.state`` populated). Call it at the
    top of a route handler:

        auth_error = await authenticate_request(request)
        if auth_error is not None:
            return auth_error
    """
    try:
        require_jwt(request)
        return None
    except HTTPException as exc:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "code": "UNAUTHORIZED",
                "status_code": status.HTTP_401_UNAUTHORIZED,
                "message": exc.detail,
            },
        )


# ---------------------------------------------------------------------------
# jwt_handler — async FastAPI dependency (kept for backwards compatibility)
# ---------------------------------------------------------------------------

async def jwt_handler(request: Request) -> Dict[str, Any]:
    """Verify the request JWT and expose its claims through ``request.state``.

    Tokens are accepted from either ``Authorization: Bearer <token>`` or the
    ``access_token`` cookie.  Keeping this dependency outside ``src.auth`` lets
    non-auth routers protect themselves without changing the auth module.
    """
    require_jwt(request)
    return request.state.claims