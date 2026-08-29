from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, Response

from src.auth.schema import (
    AuthTokensResponse,
    ChangePasswordRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    ResendVerificationOTPRequest,
    ResetPasswordRequest,
    SignInRequest,
    SignUpRequest,
    UserProfile,
    UserTaskInsightsResponse,
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
    set_access_token_cookie,
    set_refresh_token_cookie,
    string_to_bool,
    success_response,
    UserProfileFromModel,
)
from src.utils.setting import get_settings


router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Authentication"],
)


# ============================================================
# Service Dependency
# ============================================================

def get_auth_service(db=Depends(get_db), redis=Depends(get_redis)) -> AuthService:
    return AuthService(db=db, redis=redis)


# ============================================================
# Signup
# ============================================================

@router.post("/signup", status_code=201)
async def signup(req: SignUpRequest, service: AuthService = Depends(get_auth_service)):
    """Register a new user."""

    try:
        result, err = await service.signup(
            email=req.email,
            password=req.password,
            full_name=req.full_name,
            username=req.username,
            timezone=req.timezone,
            avatar_url=req.avatar_url,
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
            str(e) or "An unexpected error occurred during signup",
            status_code=500,
        )


# ============================================================
# Signin
# ============================================================

@router.post("/signin", response_model=AuthTokensResponse)
async def signin(req: SignInRequest, response: Response, service: AuthService = Depends(get_auth_service)):
    """Authenticate user and return tokens."""

    try:
        result, err = await service.signin(
            email=req.email,
            password=req.password,
            platform="web",
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

        return result
    except Exception as e:
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during signin",
            status_code=500,
        )


# ============================================================
# Refresh Token
# ============================================================

@router.post("/refresh", response_model=AuthTokensResponse)
async def refresh_token(
    req: RefreshTokenRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """Generate a new access token using refresh token."""

    try:
        result, err = await service.refresh_token(req.refresh_token)

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

        return result
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

@router.post("/verify-email")
async def verify_email(req: VerifyEmailRequest, response: Response, service: AuthService = Depends(get_auth_service)):
    """Verify user's email using OTP."""

    try:
        result, err = await service.verify_email(email=req.email, otp=req.otp)

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

        return result
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
async def validate(type: str = "email", value: str = "", service: AuthService = Depends(get_auth_service)):
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

        return {
            "success": available,
            "status_code": status_code,
            "message": message,
            "data": {
                "type": validation_type,
                "value": value,
                "available": available,
            },
        }
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

@router.put("/me")
async def update_user(req: UpdateUserRequest, current_user: dict = Depends(get_current_user), service: AuthService = Depends(get_auth_service)):
    """Update authenticated user's profile."""

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

        result, err = await service.update_user(
            user_id=user_uuid,
            full_name=getattr(req, "full_name", None),
            username=getattr(req, "username", None),
            avatar_url=getattr(req, "avatar_url", None),
            timezone=getattr(req, "timezone", None),
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
