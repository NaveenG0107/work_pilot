import asyncio
from datetime import datetime, timezone
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError

from src.auth.schema import (
    AuthTokensResponse,
    AuthTokenSuccessResponse,
    AuthSuccessResponse,
    ChangePasswordRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    ResendVerificationOTPRequest,
    ResetPasswordRequest,
    SignInRequest,
    SignUpRequest,
    UserProfile,
    UserTaskInsightsResponse,
    UpdateUserSuccessResponse,
    UpdateUserRequest,
    VerifyEmailRequest,
)
from src.auth.deps import get_current_user
from src.auth.service import AuthService
from src.database import get_db, get_redis
from src.utils.core import (
    clear_cookies,
    error_response,
    ErrorCode,
    GoJSONResponse,
    set_access_token_cookie,
    set_refresh_token_cookie,
    string_to_bool,
    success_response,
    UserProfileFromModel,
)
from src.utils.setting import get_settings
from src.utils.storage import (
    StorageConfigurationError,
    delete_s3_object,
    upload_s3_object,
)


def auth_failure(code: ErrorCode | str, message: str, status_code: int):
    return GoJSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": code.value if isinstance(code, ErrorCode) else code,
                "status_code": status_code,
                "message": message,
            },
        },
    )


# Keep every Auth error compatible with Go without changing the shared helper.
error_response = auth_failure


class AuthRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                first_error = exc.errors()[0]
                field = str(first_error.get("loc", ("field",))[-1]).replace(
                    "_", " "
                ).title()
                message = (
                    f"{field} is required."
                    if first_error.get("type") == "missing"
                    else "Invalid request payload."
                )
                return auth_failure(ErrorCode.ErrValidation, message, 400)
            except HTTPException as exc:
                code = (
                    ErrorCode.ErrUnauthorized
                    if exc.status_code == 401
                    else ErrorCode.ErrForbidden
                )
                return auth_failure(code, str(exc.detail), exc.status_code)

        return handler


router = APIRouter(
    tags=["Authentication"],
    prefix="/auth",
    route_class=AuthRoute,
)


# ============================================================
# Service Dependency
# ============================================================

def get_auth_service(db=Depends(get_db), redis=Depends(get_redis)) -> AuthService:
    return AuthService(db=db, redis=redis)


class AvatarUploadError(Exception):
    def __init__(self, status_code: int, code: ErrorCode | str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def upload_avatar(avatar: UploadFile) -> tuple[str, str]:
    max_size_mb = int(get_settings().s3_max_file_size_mb or 5)
    file_bytes = await avatar.read(max_size_mb * 1024 * 1024 + 1)
    if len(file_bytes) > max_size_mb * 1024 * 1024:
        raise AvatarUploadError(
            400,
            ErrorCode.ErrValidation,
            f"File exceeds the maximum allowed size of {max_size_mb} MB.",
        )

    if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        mime_type, extension = "image/png", "png"
    elif file_bytes.startswith(b"\xff\xd8\xff"):
        mime_type, extension = "image/jpeg", "jpg"
    elif (
        len(file_bytes) >= 12
        and file_bytes.startswith(b"RIFF")
        and file_bytes[8:12] == b"WEBP"
    ):
        mime_type, extension = "image/webp", "webp"
    else:
        raise AvatarUploadError(
            400,
            ErrorCode.ErrValidation,
            "Invalid file type. Only PNG, JPG/JPEG, and WEBP images are accepted.",
        )

    storage_key = f"users/avatars/{uuid.uuid4()}.{extension}"
    try:
        avatar_url = await asyncio.to_thread(
            upload_s3_object,
            file_bytes,
            storage_key,
            mime_type,
        )
    except StorageConfigurationError as exc:
        raise AvatarUploadError(
            503,
            "SERVICE_UNAVAILABLE",
            "Supabase S3 storage is not configured.",
        ) from exc
    except Exception as exc:
        raise AvatarUploadError(
            500,
            ErrorCode.ErrInternalServerError,
            "Failed to upload file. Please try again later.",
        ) from exc

    return avatar_url, storage_key


# ============================================================
# Signup
# ============================================================

@router.post(
    "/signup",
    status_code=201,
    response_model=AuthSuccessResponse,
)
async def signup(
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    username: str = Form(...),
    timezone: str | None = Form(default=None),
    avatar: UploadFile | None = File(default=None),
    service: AuthService = Depends(get_auth_service),
):
    """Register a new user."""

    uploaded_key: str | None = None
    try:
        try:
            req = SignUpRequest(
                email=email,
                password=password,
                full_name=full_name,
                username=username,
                timezone=timezone,
            )
        except ValidationError as exc:
            error = exc.errors()[0]
            field = str(error.get("loc", ("field",))[-1]).replace("_", " ").title()
            return auth_failure(
                ErrorCode.ErrValidation,
                f"{field} is invalid.",
                status_code=400,
            )

        avatar_url = None
        if avatar is not None:
            avatar_url, uploaded_key = await upload_avatar(avatar)

        result, err = await service.signup(
            email=req.email,
            password=req.password,
            full_name=req.full_name,
            username=req.username,
            timezone=req.timezone,
            avatar_url=avatar_url,
        )

        if err:
            if uploaded_key:
                await asyncio.to_thread(delete_s3_object, uploaded_key)
            return auth_failure(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return result
    except AvatarUploadError as exc:
        return auth_failure(exc.code, exc.message, exc.status_code)
    except Exception as e:
        if uploaded_key:
            try:
                await asyncio.to_thread(delete_s3_object, uploaded_key)
            except Exception:
                pass
        return auth_failure(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during signup",
            status_code=500,
        )


# ============================================================
# Signin
# ============================================================

@router.post("/signin", response_model=AuthTokenSuccessResponse)
async def signin(
    req: SignInRequest,
    response: Response,
    platform: str | None = Header(default=None, alias="X-Client-Platform"),
    service: AuthService = Depends(get_auth_service),
):
    """Authenticate user and return tokens."""

    try:
        platform = platform or "web"
        if platform not in {"web", "mobile"}:
            return auth_failure(
                ErrorCode.ErrValidation,
                "Invalid X-Client-Platform header. Supported platforms: web, mobile",
                status_code=400,
            )

        result, err = await service.signin(
            email=req.email,
            password=req.password,
            platform=platform,
        )

        if err:
            return auth_failure(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        settings = get_settings()

        secure, _ = string_to_bool(settings.cookie_secure or "")
        access_expires = int(settings.jwt_expiry or 900)
        refresh_expires = int(settings.refresh_token_expiry or 604800)

        set_access_token_cookie(
            response,
            result.access_token,
            access_expires,
            secure,
        )

        set_refresh_token_cookie(
            response,
            result.refresh_token,
            refresh_expires,
            secure,
        )

        return {
            "success": True,
            "status_code": 200,
            "message": "Successfully Logged in",
            "data": result.model_dump(mode="json"),
        }
    except Exception as e:
        return auth_failure(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during signin",
            status_code=500,
        )


# ============================================================
# Refresh Token
# ============================================================

@router.post("/refresh", response_model=AuthTokenSuccessResponse)
async def refresh_token(
    req: RefreshTokenRequest,
    response: Response,
    platform: str | None = Header(default=None, alias="X-Client-Platform"),
    service: AuthService = Depends(get_auth_service),
):
    """Generate a new access token using refresh token."""

    try:
        platform = platform or "web"
        if platform not in {"web", "mobile"}:
            return error_response(
                ErrorCode.ErrValidation,
                "Invalid X-Client-Platform header. Supported platforms: web, mobile",
                status_code=400,
            )

        result, err = await service.refresh_token(req.refresh_token, platform=platform)

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        settings = get_settings()

        secure, _ = string_to_bool(settings.cookie_secure or "")
        access_expires = int(settings.jwt_expiry or 900)
        refresh_expires = int(settings.refresh_token_expiry or 604800)

        set_access_token_cookie(
            response,
            result.access_token,
            access_expires,
            secure,
        )

        set_refresh_token_cookie(
            response,
            result.refresh_token,
            refresh_expires,
            secure,
        )

        return {
            "success": True,
            "status_code": 200,
            "message": "Token refreshed successfully",
            "data": result.model_dump(mode="json"),
        }
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while refreshing token",
            status_code=500,
        )


# ============================================================
# Logout
# ============================================================

@router.post("/logout")
async def logout(response: Response, current_user: dict = Depends(get_current_user), service: AuthService = Depends(get_auth_service)):
    """Revoke refresh tokens and log out the user."""

    try:
        user_id = current_user.get("user_id")

        if not user_id:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Invalid user ID",
                status_code=401,
            )

        result, err = await service.logout(user_uuid)

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        secure, _ = string_to_bool(get_settings().cookie_secure or "")

        clear_cookies(response, secure=secure)

        return result
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during logout",
            status_code=500,
        )


# ============================================================
# Change Password
# ============================================================

@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, current_user: dict = Depends(get_current_user), service: AuthService = Depends(get_auth_service)):
    """Change the password of the authenticated user."""

    try:
        user_id = current_user.get("user_id")

        if not user_id:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Invalid user ID",
                status_code=401,
            )

        result, err = await service.change_password(
            user_id=user_uuid,
            old_password=req.old_password,
            new_password=req.new_password,
        )

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return result
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while changing password",
            status_code=500,
        )


# ============================================================
# Password Reset Request
# ============================================================

@router.post("/password-reset/request")
async def password_reset_request(req: PasswordResetRequest, service: AuthService = Depends(get_auth_service)):
    """Send a password reset OTP."""

    try:
        result, err = await service.request_password_reset(email=req.email)

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return result
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while requesting password reset",
            status_code=500,
        )


# ============================================================
# Password Reset Confirm
# ============================================================

@router.post("/password-reset/confirm")
async def reset_password(req: ResetPasswordRequest, service: AuthService = Depends(get_auth_service)):
    """Validate reset OTP and update the user's password."""

    try:
        result, err = await service.reset_password(
            email=req.email,
            otp=req.otp,
            new_password=req.new_password,
        )

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return result
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while confirming password reset",
            status_code=500,
        )


# ============================================================
# Verify Email
# ============================================================

@router.post("/verify-email", response_model=AuthSuccessResponse)
async def verify_email(
    req: VerifyEmailRequest,
    response: Response,
    platform: str | None = Header(default=None, alias="X-Client-Platform"),
    service: AuthService = Depends(get_auth_service),
):
    """Verify user's email using OTP."""

    try:
        platform = platform or "web"
        if platform not in {"web", "mobile"}:
            return error_response(
                ErrorCode.ErrValidation,
                "Invalid X-Client-Platform header. Supported platforms: web, mobile",
                status_code=400,
            )

        result, err = await service.verify_email(
            email=req.email,
            otp=req.otp,
            platform=platform,
        )

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        settings = get_settings()

        secure, _ = string_to_bool(settings.cookie_secure or "")
        access_expires = int(settings.jwt_expiry or 900)
        refresh_expires = int(settings.refresh_token_expiry or 604800)

        set_access_token_cookie(
            response,
            result.access_token,
            access_expires,
            secure,
        )

        set_refresh_token_cookie(
            response,
            result.refresh_token,
            refresh_expires,
            secure,
        )

        return {
            "success": True,
            "status_code": 200,
            "message": "Email verified successfully",
        }
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while verifying email",
            status_code=500,
        )


# ============================================================
# Resend Verification OTP
# ============================================================

@router.post("/resend-verification-otp")
async def resend_verification_otp(req: ResendVerificationOTPRequest, service: AuthService = Depends(get_auth_service)):
    """Send a new verification OTP."""

    try:
        result, err = await service.resend_verification_otp(email=req.email)

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return result
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while resending verification OTP",
            status_code=500,
        )


# ============================================================
# Validate Email / Username
# ============================================================

@router.get("/validate")
async def validate(type: str = "", value: str = "", service: AuthService = Depends(get_auth_service)):
    """Check whether an email or username is available."""

    try:
        validation_type = type.lower().strip()

        if not validation_type:
            return error_response(
                ErrorCode.ErrValidation,
                "Type is required.",
                status_code=400,
            )

        if not value:
            return error_response(
                ErrorCode.ErrValidation,
                "Value is required.",
                status_code=400,
            )

        if validation_type == "email":
            available, err = await service.is_email_available(value)

        elif validation_type == "username":
            available, err = await service.is_username_available(value)

        else:
            return error_response(
                ErrorCode.ErrValidation,
                "Type must be 'email' or 'username'.",
                status_code=400,
            )

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        status_code = 200 if available else 409

        message = (
            f"{validation_type} is available."
            if available
            else f"{validation_type} is already taken."
        )

        return GoJSONResponse(
            status_code=status_code,
            content={
                "success": available,
                "status_code": status_code,
                "message": message,
                "data": {
                    "type": validation_type,
                    "value": value,
                    "available": available,
                },
            },
        )
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during validation",
            status_code=500,
        )


# ============================================================
# Get User Insights
# ============================================================

@router.get("/me/insights")
async def get_user_insights(current_user: dict = Depends(get_current_user), service: AuthService = Depends(get_auth_service)):
    """Get task statistics for the current user."""

    try:
        user_id = current_user.get("user_id")

        if not user_id:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Internal server error: missing user context",
                status_code=500,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            return error_response(
                ErrorCode.ErrValidation,
                "Invalid user ID",
                status_code=400,
            )

        result, err = await service.get_user_insights(user_uuid)

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return success_response(
            message="User insights received successfully",
            status_code=200,
            success=True,
            data=result,
        )
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while fetching user insights",
            status_code=500,
        )


# ============================================================
# Update User
# ============================================================

@router.patch("/update", response_model=UpdateUserSuccessResponse)
async def update_user(
    full_name: str | None = Form(default=None),
    username: str | None = Form(default=None),
    timezone: str | None = Form(default=None),
    avatar: UploadFile | None = File(default=None),
    current_user: dict = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Update authenticated user's profile."""

    uploaded_key: str | None = None
    try:
        user_id = current_user.get("user_id")

        if not user_id:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Invalid user ID",
                status_code=401,
            )

        avatar_url = None
        if avatar is not None:
            avatar_url, uploaded_key = await upload_avatar(avatar)

        _, err = await service.update_user(
            user_id=user_uuid,
            full_name=full_name,
            username=username,
            avatar_url=avatar_url,
            timezone=timezone,
        )

        if err:
            if uploaded_key:
                await asyncio.to_thread(delete_s3_object, uploaded_key)
            return auth_failure(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return {
            "success": True,
            "status_code": 200,
            "message": "Updated profile successfully",
            "data": {"userID": str(user_uuid)},
        }
    except AvatarUploadError as exc:
        return auth_failure(exc.code, exc.message, exc.status_code)
    except Exception as e:
        if uploaded_key:
            try:
                await asyncio.to_thread(delete_s3_object, uploaded_key)
            except Exception:
                pass
        return auth_failure(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while updating user profile",
            status_code=500,
        )


# ============================================================
# Get Current User (GET /me)
# ============================================================

@router.get("/me")
async def get_user(current_user: dict = Depends(get_current_user), service: AuthService = Depends(get_auth_service)):
    """Return the profile of the authenticated user."""

    try:
        user_id = current_user.get("user_id")

        if not user_id:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Invalid user ID",
                status_code=401,
            )

        user, err = await service.get_user(user_uuid)

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return success_response(
            message="User detail received successfully",
            status_code=200,
            success=True,
            data=UserProfileFromModel(user),
        )
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while fetching user profile",
            status_code=500,
        )


# ============================================================
# Get User By ID
# ============================================================

@router.get("/{user_id}")
async def get_user_by_id(user_id: str, current_user: dict = Depends(get_current_user), service: AuthService = Depends(get_auth_service)):
    """Get user details within the authenticated user's organization."""

    try:
        organization_id = current_user.get("organization_id")

        if not organization_id:
            return error_response(
                ErrorCode.ErrForbidden,
                "Internal server error: missing organization context",
                status_code=500,
            )

        try:
            target_uuid = uuid.UUID(user_id)
            org_uuid = uuid.UUID(organization_id)
        except ValueError:
            return error_response(
                ErrorCode.ErrValidation,
                "Invalid user ID or organization ID",
                status_code=400,
            )

        result, err = await service.get_user_by_id(user_id=target_uuid, organization_id=org_uuid)

        if err:
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        return success_response(
            message="User detail received successfully",
            status_code=200,
            success=True,
            data=UserProfileFromModel(result),
        )
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while fetching user by ID",
            status_code=500,
        )
