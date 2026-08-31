# src/core/utils.py
from __future__ import annotations

import re
import string
import secrets
import uuid
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional, Union, Dict, Tuple

import bcrypt
import jwt  # PyJWT
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth.schema import UserProfile

from src.utils.setting import get_settings

# ---------------------------------------------------------------------------
# ErrorCode enum (matching Go internal/pkg/response/error.go)
# ---------------------------------------------------------------------------

class ErrorCode(str, Enum):
    ErrBadRequest = "BAD_REQUEST"
    ErrValidation = "VALIDATION_ERROR"
    ErrUnauthorized = "UNAUTHORIZED"
    ErrForbidden = "FORBIDDEN"
    ErrNotFound = "RESOURCE_NOT_FOUND"
    ErrConflict = "CONFLICT"
    ErrRateLimitExceeded = "RATE_LIMIT_EXCEEDED"
    ErrInternalServerError = "INTERNAL_SERVER_ERROR"
    # extend with: Gone, BusinessRule, ServiceUnavailable, GatewayTimeout


# ---------------------------------------------------------------------------
# Response envelopes
# ---------------------------------------------------------------------------

def success_response(
    message: str,
    status_code: int = 200,
    success: bool = True,
    data: Any = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Success envelope: {success, status_code, message, data}"""
    return {
        "success": success,
        "status_code": status_code,
        "message": message,
        "data": data,
        "meta": meta,
    }


class APIError(JSONResponse):
    """Error envelope that also exposes .code/.message/.status_code as attributes.

    Services return this as the ``err`` half of a (result, err) tuple; ``api.py``
    reads it both as a JSONResponse and via attributes (``err.code``, ``err.message``).
    """

    def __init__(self, code, message, status_code, **kwargs):
        self._code = code
        self._message = message
        self._status_code = status_code
        content = {
            "success": False,
            "error": code,
            "message": message,
            "status_code": status_code,
        }
        content.update(kwargs)
        super().__init__(status_code=status_code, content=content)

    @property
    def code(self):
        return self._code

    @property
    def message(self):
        return self._message


def error_response(
    code: Union[ErrorCode, str],
    message: str,
    status_code: int,
) -> APIError:
    """Error envelope: {success: false, error: code, message, status_code}"""
    if isinstance(code, ErrorCode):
        code = code.value
    return APIError(code, message, status_code)


# ---------------------------------------------------------------------------
# bcrypt helpers (rounds=10 to match Go bcrypt.DefaultCost)
# ---------------------------------------------------------------------------

def bcrypt_hash(password: str) -> Tuple[str, None]:
    """Hash password with bcrypt cost 10 (Go DefaultCost). Returns (hashed_pw, None)."""
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
    return hashed, None


def bcrypt_verify(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash. Returns True/False."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Password policy validation (Go: ValidatePassword regex)
# ---------------------------------------------------------------------------

def validate_password(password: str) -> bool:
    """Returns True if password meets policy: >=8 chars, upper+lower+digit+special, no spaces."""
    if len(password) < 8:
        return False
    if "\\s" in password or re.search(r"\s", password):
        return False
    has_upper = re.search(r"[A-Z]", password) is not None
    has_lower = re.search(r"[a-z]", password) is not None
    has_digit = re.search(r"[0-9]", password) is not None
    # Special chars: ! @ # $ % ^ & * ( ) _ + - = [ ] { } | \ : ; " ' < > , . ? / ~
    has_special = (
        re.search(r'[!@#$%^&*()_+\-=\[\]{}|\\:;"\'<>,.?/~]', password)
        is not None
    )
    return has_upper and has_lower and has_digit and has_special


# ---------------------------------------------------------------------------
# OTP generation
# ---------------------------------------------------------------------------

def generate_otp_6digit() -> str:
    """Generate a 6-digit numeric OTP."""
    return "".join(secrets.choice(string.digits) for _ in range(6))


def generate_refresh_secret_64hex() -> str:
    """Generate a 64-character hex string (256-bit secret)."""
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# JWT helpers (HS256, platform-aware expiry)
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
# String conversion helpers
# ---------------------------------------------------------------------------

def string_to_uuid(id_str: str) -> Tuple[Union[uuid.UUID, None], Optional[Dict[str, Any]]]:
    """Convert string to UUID. Returns (uuid_or_None, error_or_None)."""
    try:
        return uuid.UUID(id_str), None
    except (ValueError, TypeError):
        return None, {"error": "Invalid UUID format", "code": "BAD_REQUEST"}


def string_to_bool(s: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Convert 'true'/'false' string to bool."""
    s_lower = s.strip().lower()
    if s_lower in ("true", "1", "yes"):
        return True, None
    if s_lower in ("false", "0", "no"):
        return False, None
    return False, {"error": f"Cannot convert '{s}' to bool", "code": "BAD_REQUEST"}


# ---------------------------------------------------------------------------
# Redis key builders (matching Go repo key patterns)
# ---------------------------------------------------------------------------

def build_redis_key_user_email(email: str) -> str:
    """Key for temp user storage by email: user:email:<lowercase_email>"""
    return f"user:email:{email.lower().strip()}"


def build_redis_key_pwd_otp(user_id: uuid.UUID) -> str:
    """Key for password-reset OTP: password-reset-otp:<user_id>"""
    return f"password-reset-otp:{user_id}"


def build_redis_key_email_resend(email: str) -> str:
    """Key for email-verification resend rate-limit: email-verification-resend:<email>"""
    return f"email-verification-resend:{email.lower().strip()}"


# ---------------------------------------------------------------------------
# Cookie helpers (FastAPI Response)
# ---------------------------------------------------------------------------

def set_access_token_cookie(response: Response, token: str, max_age: int, secure: bool) -> None:
    """Set HttpOnly access_token cookie."""
    import http.cookies as http_cookie
    cookie = http_cookie.SimpleCookie()
    cookie["access_token"] = token
    cookie["access_token"]["httponly"] = True
    cookie["access_token"]["path"] = "/"
    cookie["access_token"]["secure"] = secure
    cookie["access_token"]["samesite"] = "lax"
    cookie["access_token"]["max-age"] = max_age
    response.headers.append("Set-Cookie", cookie.output(header="").lstrip("\r\n"))


def set_refresh_token_cookie(response: Response, token: str, max_age: int, secure: bool) -> None:
    """Set HttpOnly refresh_token cookie."""
    import http.cookies as http_cookie
    cookie = http_cookie.SimpleCookie()
    cookie["refresh_token"] = token
    cookie["refresh_token"]["httponly"] = True
    cookie["refresh_token"]["path"] = "/"
    cookie["refresh_token"]["secure"] = secure
    cookie["refresh_token"]["samesite"] = "lax"
    cookie["refresh_token"]["max-age"] = max_age
    response.headers.append("Set-Cookie", cookie.output(header="").lstrip("\r\n"))


def clear_cookies(response: Response, secure: bool) -> None:
    """Clear both access_token and refresh_token cookies."""
    set_access_token_cookie(response, "", -1, secure)
    set_refresh_token_cookie(response, "", -1, secure)

def generate_pw_otp(length: int = 6) -> str:
    """
    Generate a numeric OTP of the given length (default 6).

    Alias used by the auth service (kept for naming consistency with the
    Go implementation's generateOTP helper).
    """
    return generate_otpw_otp(length)


def generate_otpw_otp(length: int = 6) -> str:
    """
    Generate a numeric OTP of the given length (default 6).
    Matches Go's generateOTP(length int) function.
    """
    import secrets
    import string
    chars = string.digits  # "0123456789"
    return "".join(secrets.choice(chars) for _ in range(length))

def string_to_int(s: str) -> Tuple[Union[int, None], Optional[Dict[str, Any]]]:
    """
    Convert a string to int.
    Returns (int_value, error_dict_or_None).
    Matches Go's utils.StringToInt behavior.
    """
    try:
        num = int(s.strip())
        return num, None
    except (ValueError, TypeError):
        return None, {"error": f"Cannot convert '{s}' to int", "code": "BAD_REQUEST"}

def format_validation_error(err: Any, payload: Any) -> str:
    """
    Format a validation error from FastAPI/ Pydantic into a user‑friendly message.
    Matches Go's utils.ValidationErrorMessage(err, payload).
    """
    if err is None:
        return "Invalid request payload."

    from typing import getattr as get_attr
    import re

    # Try to extract validator error info
    var_errs = getattr(err, "errors", None)
    if var_errs and len(var_errs) > 0:
        field_err = var_errs[0]
        label = get_attr(payload, field_err.get("loc", [None])[-1] or "Field") or "Field"

        tag = field_err.get("get", lambda: None)("")  # simplified tag lookup
        param = field_err.get("get", lambda: None)("").get("param", "")

        switch_map = {
            "required": f"{label} is required.",
            "email": f"{label} must be a valid email address.",
            "max": f"{label} must not exceed {param} characters.",
            "min": f"{label} must be at least {param} characters.",
            "oneof": f"{label} must be one of {param}.",
        }

        handler = switch_map.get(tag)
        if handler:
            return handler

    # Fallback
    return "Invalid request payload."

def hash_password(password: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Hash a password using bcrypt with cost 10 (Go DefaultCost).
    Returns (hashed_password, error_or_None).
    Matches internal/pkg/utils.HashPassword Go function.
    """
    import bcrypt
    try:
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
        return hashed, None
    except Exception as e:
        return "", {"error": str(e), "code": "INTERNAL_SERVER_ERROR"}

def ParseUserDuplicateError(err: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parse a database duplicate‑key error and return a response.Error dict.
    Corresponds to Go's utils.ParseUserDuplicateError(err error) *response.Error.
    """
    if not err:
        return None

    err_msg = err.lower()

    if "username" in err_msg or "idx_users_username" in err_msg:
        return {"code": "CONFLICT", "status_code": 409, "message": "Username is already taken"}

    if "email" in err_msg or "idx_users_email" in err_msg:
        return {"code": "CONFLICT", "status_code": 409, "message": "User with this email already exists"}

    # Default conflict fallback
    return {"code": "CONFLICT", "status_code": 409, "message": "User with this email or username already exists"}

def UserProfileFromModel(user_model: Any) -> UserProfile:
    """
    Convert an ORM User model (SQLAlchemy) into a Pydantic UserProfile.
    Matches internal/handlers/dto/response/mapping.go's UserProfileFromModel.
    """
    # Derive avatar URL helper
    avatar_url = None
    if hasattr(user_model, "avatar_url") and user_model.avatar_url:
        avatar_url = user_model.avatar_url

    return UserProfile(
        id=str(user_model.id),
        organization_id=str(user_model.organization_id) if user_model.organization_id else None,
        organization_name=getattr(user_model, "organization_name", None) or (user_model.organization.name if hasattr(user_model, "organization") else None),
        name=user_model.full_name,
        username=user_model.username,
        email=user_model.email,
        role=user_model.role.name if user_model.role else "member",
        avatar_url=avatar_url,
        color=user_model.color or "#3498DB",
        timezone=user_model.timezone or "UTC",
        is_active=user_model.is_active,
        is_verified=user_model.is_verified,
        status=user_model.status,
        created_at=user_model.created_at,
        joined_at=getattr(user_model, "joined_at", None),
        require_password_change=user_model.require_password_change,
    )


# ---------------------------------------------------------------------------
# GoJSONResponse — Gin content type (application/json; charset=utf-8)
# ---------------------------------------------------------------------------

class GoJSONResponse(JSONResponse):
    """JSON response with the content type emitted by Gin."""

    media_type = "application/json; charset=utf-8"


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
    ``access_token`` cookie. Keeping this dependency inside ``core`` lets
    non-auth routers protect themselves without changing the auth module.
    """
    require_jwt(request)
    return request.state.claims