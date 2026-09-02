import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response

from src.auth.deps import get_current_user
from src.auth.schema import (
    AuthTokensResponse,
    ChangePasswordRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    ResendVerificationOTPRequest,
    ResetPasswordRequest,
    SignInRequest,
    SignUpRequest,
    UpdateUserRequest,
    UserProfile,
    UserTaskInsightsResponse,
    VerifyEmailRequest,
)
from src.auth.service import AuthService
from src.config import get_logger
from src.database import get_db, get_redis
from src.utils.core import (
    ErrorCode,
    UserProfileFromModel,
    clear_cookies,
    error_response,
    set_access_token_cookie,
    set_refresh_token_cookie,
    string_to_bool,
    success_response,
)
from src.utils.setting import get_settings

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Authentication"],
)


def get_auth_service(db=Depends(get_db), redis=Depends(get_redis)) -> AuthService:
    return AuthService(db=db, redis=redis)


@router.post("/signup", status_code=201)
async def signup(
    req: SignUpRequest,
    service: AuthService = Depends(get_auth_service),
):
    """Register a new user account."""

    try:
        logger.info("Received signup request for email: %s", req.email)

        result, err = await service.signup(
            email=req.email,
            password=req.password,
            full_name=req.full_name,
            username=req.username,
            timezone=req.timezone,
            avatar_url=req.avatar_url,
        )

        if err:
            logger.warning("Signup failed for email: %s, error: %s", req.email, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("Successfully registered pending user for email: %s", req.email)
        return result

    except Exception as e:
        logger.error("Unexpected error during signup for email %s: %s", req.email, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during signup",
            status_code=500,
        )


@router.post("/signin", response_model=AuthTokensResponse)
async def signin(
    req: SignInRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """Authenticate user and return tokens."""

    try:
        logger.info("Received signin request for email: %s", req.email)

        result, err = await service.signin(
            email=req.email,
            password=req.password,
            platform="web",
        )

        if err:
            logger.warning("Signin authentication failed for email: %s, error: %s", req.email, err.message)
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

        logger.info("User signed in successfully: email=%s", req.email)
        return result

    except Exception as e:
        logger.error("Unexpected error during signin for email %s: %s", req.email, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during signin",
            status_code=500,
        )


@router.post("/refresh", response_model=AuthTokensResponse)
async def refresh_token(
    req: RefreshTokenRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """Generate a new access token using refresh token."""

    try:
        logger.info("Received token refresh request")

        result, err = await service.refresh_token(req.refresh_token)

        if err:
            logger.warning("Token refresh validation failed: %s", err.message)
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

        logger.info("Session tokens refreshed successfully")
        return result

    except Exception as e:
        logger.error("Unexpected error during token refresh: %s", str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while refreshing token",
            status_code=500,
        )


@router.post("/logout")
async def logout(
    response: Response,
    current_user: dict = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Revoke refresh tokens and log out the user."""

    try:
        user_id = current_user.get("user_id")
        logger.info("Received logout request for user_id: %s", user_id)

        if not user_id:
            logger.warning("Logout attempt without active user_id context")
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            logger.warning("Invalid user_id format during logout: %s", user_id)
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Invalid user ID",
                status_code=401,
            )

        result, err = await service.logout(user_uuid)

        if err:
            logger.warning("Logout operation failed for user_id %s: %s", user_id, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        secure, _ = string_to_bool(get_settings().cookie_secure or "")
        clear_cookies(response, secure=secure)

        logger.info("User logged out successfully: user_id=%s", user_id)
        return result

    except Exception as e:
        logger.error("Unexpected error during logout for user_id %s: %s", current_user.get("user_id"), str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during logout",
            status_code=500,
        )


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Change the password of the authenticated user."""

    try:
        user_id = current_user.get("user_id")
        logger.info("Received change-password request for user_id: %s", user_id)

        if not user_id:
            logger.warning("Password change attempt without active user_id context")
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            logger.warning("Invalid user_id format during password change: %s", user_id)
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
            logger.warning("Password change failed for user_id %s: %s", user_id, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("Password changed successfully for user_id: %s", user_id)
        return result

    except Exception as e:
        logger.error("Unexpected error during password change for user_id %s: %s", current_user.get("user_id"), str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while changing password",
            status_code=500,
        )


@router.post("/password-reset/request")
async def password_reset_request(
    req: PasswordResetRequest,
    service: AuthService = Depends(get_auth_service),
):
    """Send a password reset OTP."""

    try:
        logger.info("Received password reset request for email: %s", req.email)

        result, err = await service.request_password_reset(email=req.email)

        if err:
            logger.warning("Password reset request failed for email %s: %s", req.email, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("Password reset OTP sent successfully for email: %s", req.email)
        return result

    except Exception as e:
        logger.error("Unexpected error requesting password reset for email %s: %s", req.email, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while requesting password reset",
            status_code=500,
        )


@router.post("/password-reset/confirm")
async def reset_password(
    req: ResetPasswordRequest,
    service: AuthService = Depends(get_auth_service),
):
    """Validate reset OTP and update the user's password."""

    try:
        logger.info("Received password reset confirmation for email: %s", req.email)

        result, err = await service.reset_password(
            email=req.email,
            otp=req.otp,
            new_password=req.new_password,
        )

        if err:
            logger.warning("Password reset confirmation failed for email %s: %s", req.email, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("Password reset confirmed successfully for email: %s", req.email)
        return result

    except Exception as e:
        logger.error("Unexpected error confirming password reset for email %s: %s", req.email, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while confirming password reset",
            status_code=500,
        )


@router.post("/verify-email")
async def verify_email(
    req: VerifyEmailRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """Verify user's email using OTP."""

    try:
        logger.info("Received email verification request for email: %s", req.email)

        result, err = await service.verify_email(email=req.email, otp=req.otp)

        if err:
            logger.warning("Email verification failed for email %s: %s", req.email, err.message)
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

        logger.info("Email verified successfully for email: %s", req.email)
        return result

    except Exception as e:
        logger.error("Unexpected error verifying email %s: %s", req.email, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while verifying email",
            status_code=500,
        )


@router.post("/resend-verification-otp")
async def resend_verification_otp(
    req: ResendVerificationOTPRequest,
    service: AuthService = Depends(get_auth_service),
):
    """Send a new verification OTP."""

    try:
        logger.info("Received resend verification OTP request for email: %s", req.email)

        result, err = await service.resend_verification_otp(email=req.email)

        if err:
            logger.warning("Resend verification OTP failed for email %s: %s", req.email, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("Verification OTP resent successfully to email: %s", req.email)
        return result

    except Exception as e:
        logger.error("Unexpected error resending verification OTP for email %s: %s", req.email, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while resending verification OTP",
            status_code=500,
        )


@router.get("/validate")
async def validate(
    type: str = "email",
    value: str = "",
    service: AuthService = Depends(get_auth_service),
):
    """Check whether an email or username is available."""

    try:
        validation_type = type.lower().strip()
        logger.info("Received validation check request: type=%s, value=%s", validation_type, value)

        if not validation_type:
            logger.warning("Validation rejected: missing type parameter")
            return error_response(
                ErrorCode.ErrValidation,
                "Type is required.",
                status_code=400,
            )

        if not value:
            logger.warning("Validation rejected: missing value parameter")
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
            logger.warning("Validation rejected: invalid validation type=%s", validation_type)
            return error_response(
                ErrorCode.ErrValidation,
                "Type must be 'email' or 'username'.",
                status_code=400,
            )

        if err:
            logger.warning("Validation lookup error for type %s: %s", validation_type, err.message)
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

        logger.info("Validation check completed: type=%s, value=%s, available=%s", validation_type, value, available)
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
        logger.error("Unexpected error during validation check for type %s: %s", type, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred during validation",
            status_code=500,
        )


@router.get("/me/insights")
async def get_user_insights(
    current_user: dict = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Get task statistics for the current user."""

    try:
        user_id = current_user.get("user_id")
        logger.info("Received request for user insights: user_id=%s", user_id)

        if not user_id:
            logger.warning("User insights rejected: missing user context")
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Internal server error: missing user context",
                status_code=500,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            logger.warning("User insights rejected: invalid UUID user_id=%s", user_id)
            return error_response(
                ErrorCode.ErrValidation,
                "Invalid user ID",
                status_code=400,
            )

        result, err = await service.get_user_insights(user_uuid)

        if err:
            logger.warning("Failed to fetch user insights for user_id %s: %s", user_id, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("User insights retrieved successfully: user_id=%s", user_id)
        return success_response(
            message="User insights received successfully",
            status_code=200,
            success=True,
            data=result,
        )

    except Exception as e:
        logger.error("Unexpected error fetching user insights for user_id %s: %s", current_user.get("user_id"), str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while fetching user insights",
            status_code=500,
        )


@router.patch("/update")
@router.put("/me")
async def update_user(
    req: UpdateUserRequest,
    current_user: dict = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Update authenticated user's profile."""

    try:
        user_id = current_user.get("user_id")
        logger.info("Received profile update request for user_id: %s", user_id)

        if not user_id:
            logger.warning("Profile update rejected: missing user_id context")
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            logger.warning("Invalid user_id format during profile update: %s", user_id)
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
            logger.warning("Profile update failed for user_id %s: %s", user_id, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("User profile updated successfully: user_id=%s", user_id)
        return result

    except Exception as e:
        logger.error("Unexpected error updating user profile for user_id %s: %s", current_user.get("user_id"), str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while updating user profile",
            status_code=500,
        )


@router.get("/me")
async def get_user(
    current_user: dict = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Return the profile of the authenticated user."""

    try:
        user_id = current_user.get("user_id")
        logger.info("Received request for current user profile: user_id=%s", user_id)

        if not user_id:
            logger.warning("Get current user profile rejected: missing user_id context")
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Authentication required",
                status_code=401,
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            logger.warning("Invalid user_id format during profile fetch: %s", user_id)
            return error_response(
                ErrorCode.ErrUnauthorized,
                "Invalid user ID",
                status_code=401,
            )

        user, err = await service.get_user(user_uuid)

        if err:
            logger.warning("Failed to fetch user profile for user_id %s: %s", user_id, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("Current user profile retrieved successfully: user_id=%s", user_id)
        return success_response(
            message="User detail received successfully",
            status_code=200,
            success=True,
            data=UserProfileFromModel(user),
        )

    except Exception as e:
        logger.error("Unexpected error fetching profile for user_id %s: %s", current_user.get("user_id"), str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while fetching user profile",
            status_code=500,
        )


@router.get("/{user_id}")
async def get_user_by_id(
    user_id: str,
    current_user: dict = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """Get user details within the authenticated user's organization."""

    try:
        organization_id = current_user.get("organization_id")
        logger.info("Received request for user by ID: target_user_id=%s, organization_id=%s", user_id, organization_id)

        if not organization_id:
            logger.warning("Get user by ID rejected: missing organization_id context")
            return error_response(
                ErrorCode.ErrForbidden,
                "Internal server error: missing organization context",
                status_code=500,
            )

        try:
            target_uuid = uuid.UUID(user_id)
            org_uuid = uuid.UUID(organization_id)
        except ValueError:
            logger.warning("Invalid UUID during user lookup: user_id=%s, org_id=%s", user_id, organization_id)
            return error_response(
                ErrorCode.ErrValidation,
                "Invalid user ID or organization ID",
                status_code=400,
            )

        result, err = await service.get_user_by_id(user_id=target_uuid, organization_id=org_uuid)

        if err:
            logger.warning("Failed to fetch target user_id %s in org %s: %s", user_id, organization_id, err.message)
            return error_response(
                err.code,
                err.message,
                status_code=err.status_code,
            )

        logger.info("Target user details retrieved successfully: user_id=%s", user_id)
        return success_response(
            message="User detail received successfully",
            status_code=200,
            success=True,
            data=UserProfileFromModel(result),
        )

    except Exception as e:
        logger.error("Unexpected error fetching target user_id %s: %s", user_id, str(e), exc_info=True)
        return error_response(
            ErrorCode.ErrInternalServerError,
            str(e) or "An unexpected error occurred while fetching user by ID",
            status_code=500,
        )
