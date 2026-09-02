from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Optional, Union

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.audit.models import AuditLog
from src.auth.models import RefreshToken, User
from src.auth.schema import AuthTokensResponse
from src.config import get_logger
from src.custom_status.models import CustomStatus
from src.database import get_db, get_redis
from src.integrations.email import (
    send_email_verification_otp,
    send_password_reset_otp,
)
from src.organization.models import OrganizationInvitation, Role
from src.project.models import Project
from src.task.models import Task
from src.utils.core import (
    ErrorCode,
    bcrypt_hash,
    bcrypt_verify,
    create_jwt,
    error_response,
    generate_pw_otp,
    generate_refresh_secret_64hex,
    hash_password,
    success_response,
    validate_password,
)
from src.utils.setting import get_settings

logger = get_logger(__name__)


class AuthService:
    """Authentication business logic and data-access layer."""

    def __init__(
        self,
        db: Optional[AsyncSession] = None,
        redis: Any = None,
    ):
        self.db = db
        self.redis = redis

    async def _get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email with organization and role/permissions loaded."""
        result = await self.db.execute(
            select(User)
            .where(User.email == email)
            .options(
                selectinload(User.organization),
                selectinload(User.role).selectinload(Role.permissions),
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            return None

        if user.role_id is None or user.role is None:
            role_result = await self.db.execute(
                select(Role).where(
                    Role.name == "developer",
                    Role.organization_id.is_(None),
                )
            )
            role = role_result.scalar_one_or_none()

            if role:
                user.role_id = str(role.id)
                user.role = role

                await self.db.execute(
                    update(User)
                    .where(User.id == str(user.id))
                    .values(role_id=str(role.id))
                )
                await self.db.commit()

        return user

    async def _get_user_by_id(self, user_id: Union[uuid.UUID, str]) -> Optional[User]:
        """Get user by ID with organization and role loaded."""
        result = await self.db.execute(
            select(User)
            .where(User.id == str(user_id))
            .options(
                selectinload(User.organization),
                selectinload(User.role).selectinload(Role.permissions),
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            return None

        if user.role_id is None or user.role is None:
            role_result = await self.db.execute(
                select(Role).where(
                    Role.name == "developer",
                    Role.organization_id.is_(None),
                )
            )
            role = role_result.scalar_one_or_none()

            if role:
                user.role_id = str(role.id)
                user.role = role

                await self.db.execute(
                    update(User)
                    .where(User.id == str(user.id))
                    .values(role_id=str(role.id))
                )
                await self.db.commit()

        return user

    async def _get_role_by_name(self, name: str) -> Optional[Role]:
        result = await self.db.execute(
            select(Role).where(
                Role.name == name,
                Role.organization_id.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _exists_by_email(self, email: str) -> bool:
        result = await self.db.execute(
            select(func.count())
            .select_from(User)
            .where(User.email == email)
        )
        return result.scalar_one() > 0

    async def _exists_by_username(self, username: str) -> bool:
        result = await self.db.execute(
            select(func.count())
            .select_from(User)
            .where(User.username == username)
        )
        return result.scalar_one() > 0

    @staticmethod
    def _normalize_role(user: User) -> str:
        role_name = user.role.name if user.role else ""

        if role_name in ("", "developer"):
            return "member"

        return role_name

    @staticmethod
    def _serialize_data(data: dict[str, Any]) -> dict[str, Any]:
        """Convert UUID/datetime values to JSON-safe values."""
        serialized = {}

        for key, value in data.items():
            if value is None:
                serialized[key] = None
            elif isinstance(value, (datetime, uuid.UUID)):
                serialized[key] = str(value)
            elif hasattr(value, "isoformat"):
                serialized[key] = value.isoformat()
            else:
                serialized[key] = value

        return serialized

    def _user_redis_key(self, email: str) -> str:
        return f"user:email:{email.lower().strip()}"

    async def _store_temp_user(self, user: User) -> None:
        if self.redis is None:
            logger.error("Redis client is not configured when storing temp user")
            raise RuntimeError("Redis client is not configured")

        data = {
            column.name: getattr(user, column.name)
            for column in User.__table__.columns
        }
        data = self._serialize_data(data)

        await self.redis.set(
            self._user_redis_key(user.email),
            json.dumps(data),
            ex=180,
        )
        logger.info("Stored temporary user in Redis key=%s", self._user_redis_key(user.email))

    async def _get_temp_user(self, email: str) -> Optional[dict[str, Any]]:
        if self.redis is None:
            logger.error("Redis client is not configured when reading temp user")
            raise RuntimeError("Redis client is not configured")

        value = await self.redis.get(self._user_redis_key(email))

        if value is None:
            return None

        if isinstance(value, bytes):
            value = value.decode("utf-8")

        return json.loads(value)

    def _password_reset_otp_key(self, user_id: Union[uuid.UUID, str]) -> str:
        return f"password-reset-otp:{str(user_id)}"

    def _email_verification_otp_key(self, user_id: Union[uuid.UUID, str]) -> str:
        return f"email-verification-otp:{str(user_id)}"

    def _email_resend_key(self, email: str) -> str:
        return f"email-verification-resend:{email.lower().strip()}"

    async def _save_otp(
        self,
        key: str,
        user_id: Union[uuid.UUID, str],
        otp_hash: str,
        expires_at: datetime,
    ) -> None:
        if self.redis is None:
            logger.error("Redis client is not configured when saving OTP")
            raise RuntimeError("Redis client is not configured")

        data = {
            "id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "otp_hash": otp_hash,
            "expires_at": expires_at.isoformat(),
            "used_at": None,
        }
        ttl = int((expires_at - datetime.now(dt_timezone.utc)).total_seconds())

        await self.redis.set(
            key,
            json.dumps(data),
            ex=max(ttl, 1),
        )
        logger.info("Saved OTP in Redis key=%s user_id=%s", key, user_id)

    async def _get_otp(self, key: str) -> Optional[dict[str, Any]]:
        if self.redis is None:
            logger.error("Redis client is not configured when fetching OTP")
            raise RuntimeError("Redis client is not configured")

        value = await self.redis.get(key)

        if value is None:
            return None

        if isinstance(value, bytes):
            value = value.decode("utf-8")

        data = json.loads(value)
        expires_at = datetime.fromisoformat(
            data["expires_at"].replace("Z", "+00:00")
        )

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt_timezone.utc)

        if (expires_at < datetime.now(dt_timezone.utc) or data.get("used_at") is not None):
            logger.warning("OTP expired or used key=%s", key)
            await self.redis.delete(key)
            return None

        return data

    async def _invalidate_otp(self, key: str) -> None:
        if self.redis is None:
            raise RuntimeError("Redis client is not configured")

        await self.redis.delete(key)
        logger.info("Invalidated OTP key=%s", key)

    async def signup(
        self,
        email: str,
        password: str,
        full_name: str,
        username: str,
        timezone: Optional[str],
        avatar_url: Optional[str] = None,
    ):
        """Register a new user."""
        try:
            clean_email = email.lower().strip()
            clean_username = username.strip()
            logger.info("Starting signup for clean_email=%s, username=%s", clean_email, clean_username)

            if await self._exists_by_email(clean_email):
                logger.warning("Signup conflict: email=%s already exists", clean_email)
                return None, error_response(
                    ErrorCode.ErrConflict,
                    "User with this email already exists",
                    status_code=409,
                )

            if await self._exists_by_username(clean_username):
                logger.warning("Signup conflict: username=%s already taken", clean_username)
                return None, error_response(
                    ErrorCode.ErrConflict,
                    "Username is already taken",
                    status_code=409,
                )

            password_hash, _ = bcrypt_hash(password)
            developer_role = await self._get_role_by_name("developer")
            now = datetime.now(dt_timezone.utc)
            user_id_str = str(uuid.uuid4())

            user = User(
                id=user_id_str,
                email=clean_email,
                password_hash=password_hash,
                full_name=full_name,
                username=clean_username,
                role_id=str(developer_role.id) if (developer_role and developer_role.id) else None,
                is_active=False,
                is_verified=False,
                status="active",
                created_at=now,
                timezone=timezone or "UTC",
                require_password_change=False,
                avatar_url=avatar_url,
            )

            await self._store_temp_user(user)

            otp = generate_pw_otp(6)
            otp_expiry_minutes = int(get_settings().otp_expiry_minutes or 15)
            expires_at = now + timedelta(minutes=otp_expiry_minutes)
            otp_hash, _ = hash_password(otp)

            await self._invalidate_otp(self._email_verification_otp_key(user_id_str))
            await self._save_otp(
                key=self._email_verification_otp_key(user_id_str),
                user_id=user_id_str,
                otp_hash=otp_hash,
                expires_at=expires_at,
            )

            await send_email_verification_otp(
                user.email,
                otp,
                otp_expiry_minutes,
            )

            logger.info("Signup registration successful for user_id=%s email=%s", user_id_str, clean_email)
            return success_response(
                message=(
                    "Successfully Created. "
                    "Please verify your email with the OTP "
                    "sent to your inbox."
                ),
                status_code=201,
                success=True,
            ), None

        except Exception as e:
            logger.error("Error in signup service for email=%s: %s", email, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred during signup",
                status_code=500,
            )

    async def signin(
        self,
        email: str,
        password: str,
        platform: str = "web",
    ):
        """Authenticate user and generate access/refresh tokens."""
        try:
            clean_email = email.lower().strip()
            logger.info("Executing signin authentication for clean_email=%s", clean_email)

            user = await self._get_user_by_email(clean_email)

            if user is None:
                logger.warning("Signin failed: user email=%s not found", clean_email)
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Invalid email or password",
                    status_code=400,
                )

            if not user.is_active:
                inv_result = await self.db.execute(
                    select(OrganizationInvitation).where(
                        OrganizationInvitation.email == clean_email,
                        OrganizationInvitation.status == "pending",
                    )
                )
                invitation = inv_result.scalar_one_or_none()

                if (
                    invitation
                    and invitation.expires_at > datetime.now(dt_timezone.utc)
                ):
                    now = datetime.now(dt_timezone.utc)

                    user.is_active = True
                    user.status = "active"
                    user.joined_at = now

                    if not user.organization_id:
                        user.organization_id = str(invitation.organization_id)

                    user.role_id = str(invitation.role_id)
                    await self.db.commit()

                    invitation.status = "accepted"
                    invitation.accepted_at = now
                    invitation.updated_at = now

                    await self.db.commit()

                    audit = AuditLog(
                        id=str(uuid.uuid4()),
                        user_id=str(user.id),
                        organization_id=str(invitation.organization_id),
                        action="organization_invitation_accepted",
                        resource_type="organization_invitation",
                        resource_id=invitation.token,
                        details=f"Accepted invitation for {clean_email} via Sign-in",
                        created_at=now,
                    )

                    self.db.add(audit)
                    await self.db.commit()
                    logger.info("Activated pending user and accepted invitation for email=%s", clean_email)

                else:
                    logger.warning("Signin rejected: user account email=%s deactivated", clean_email)
                    return None, error_response(
                        ErrorCode.ErrForbidden,
                        (
                            "Your account has been deactivated. "
                            "Please contact support."
                        ),
                        status_code=403,
                    )

            if not user.is_verified:
                logger.warning("Signin rejected: unverified email=%s", clean_email)
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "Email address must be verified before login",
                    status_code=403,
                )

            if not bcrypt_verify(password, user.password_hash):
                logger.warning("Signin failed: incorrect password for email=%s", clean_email)
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Invalid email or password",
                    status_code=400,
                )

            role_name = self._normalize_role(user)
            organization_id = (
                str(user.organization_id)
                if user.organization_id
                else None
            )

            access_token, jwt_err = create_jwt(
                role=role_name,
                user_id=str(user.id),
                organization_id=organization_id,
                platform=platform,
            )

            if jwt_err:
                logger.error("JWT creation error for user_id=%s: %s", user.id, jwt_err.message)
                return None, error_response(
                    jwt_err.code,
                    jwt_err.message,
                    status_code=jwt_err.status_code,
                )

            refresh_secret = generate_refresh_secret_64hex()
            refresh_hash, _ = bcrypt_hash(refresh_secret)
            refresh_expiry = int(get_settings().refresh_token_expiry or 604800)
            expires_at = datetime.now(dt_timezone.utc) + timedelta(minutes=refresh_expiry)

            refresh_token = RefreshToken(
                id=str(uuid.uuid4()),
                user_id=str(user.id),
                token_hash=refresh_hash,
                expires_at=expires_at,
                revoked_at=None,
            )

            token_result = await self.db.execute(
                select(RefreshToken).where(RefreshToken.user_id == str(user.id))
            )
            existing_token = token_result.scalar_one_or_none()

            if existing_token:
                existing_token.token_hash = refresh_hash
                existing_token.expires_at = expires_at
                existing_token.revoked_at = None

                if hasattr(existing_token, "updated_at"):
                    existing_token.updated_at = datetime.now(dt_timezone.utc)

                stored_token = existing_token
            else:
                self.db.add(refresh_token)
                stored_token = refresh_token

            await self.db.commit()
            await self.db.refresh(stored_token)

            refresh_token_value = f"{stored_token.id}.{refresh_secret}"
            access_expiry = int(get_settings().jwt_expiry or 900)

            logger.info("Signin complete for user_id=%s email=%s", user.id, clean_email)
            return AuthTokensResponse(
                access_token=access_token,
                refresh_token=refresh_token_value,
                token_type="Bearer",
                expires_in=access_expiry,
                refresh_expires_in=refresh_expiry,
                require_password_change=user.require_password_change,
            ), None

        except Exception as e:
            logger.error("Error in signin service for email=%s: %s", email, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred during signin",
                status_code=500,
            )

    async def refresh_token(
        self,
        refresh_token_str: str,
        platform: str = "web",
    ):
        """Generate a new access token and refresh token."""
        try:
            logger.info("Executing refresh token validation")
            parts = refresh_token_str.split(".")

            if len(parts) != 2:
                logger.warning("Invalid refresh token structure")
                return None, error_response(
                    ErrorCode.ErrUnauthorized,
                    "Authentication required",
                    status_code=401,
                )

            token_id_str, secret = parts

            try:
                token_id = str(uuid.UUID(token_id_str))
            except ValueError:
                logger.warning("Invalid UUID in refresh token ID")
                return None, error_response(
                    ErrorCode.ErrUnauthorized,
                    "Authentication required",
                    status_code=401,
                )

            token_result = await self.db.execute(
                select(RefreshToken).where(RefreshToken.id == token_id)
            )
            old_token = token_result.scalar_one_or_none()

            if old_token is None or old_token.revoked_at is not None:
                logger.warning("Refresh token not found or revoked token_id=%s", token_id)
                return None, error_response(
                    ErrorCode.ErrUnauthorized,
                    "Authentication required",
                    status_code=401,
                )

            if not bcrypt_verify(secret, old_token.token_hash):
                logger.warning("Refresh token secret hash mismatch token_id=%s", token_id)
                return None, error_response(
                    ErrorCode.ErrUnauthorized,
                    "Authentication required",
                    status_code=401,
                )

            if datetime.now(dt_timezone.utc) > old_token.expires_at:
                logger.warning("Refresh token expired for token_id=%s", token_id)
                return None, error_response(
                    ErrorCode.ErrUnauthorized,
                    "Session has expired. Please sign in again.",
                    status_code=401,
                )

            user = await self._get_user_by_id(old_token.user_id)

            if user is None or not user.is_active:
                logger.warning("User missing or inactive for refresh token_id=%s", token_id)
                return None, error_response(
                    ErrorCode.ErrUnauthorized,
                    "Authentication required",
                    status_code=401,
                )

            role_name = self._normalize_role(user)
            organization_id = (
                str(user.organization_id)
                if user.organization_id
                else None
            )

            access_token, jwt_err = create_jwt(
                role=role_name,
                user_id=str(user.id),
                organization_id=organization_id,
                platform=platform,
            )

            if jwt_err:
                return None, error_response(
                    jwt_err.code,
                    jwt_err.message,
                    status_code=jwt_err.status_code,
                )

            new_secret = generate_refresh_secret_64hex()
            new_hash, _ = bcrypt_hash(new_secret)
            refresh_expiry = int(get_settings().refresh_token_expiry or 604800)
            new_expires_at = datetime.now(dt_timezone.utc) + timedelta(minutes=refresh_expiry)

            old_token.token_hash = new_hash
            old_token.expires_at = new_expires_at
            old_token.revoked_at = None
            if hasattr(old_token, "updated_at"):
                old_token.updated_at = datetime.now(dt_timezone.utc)

            await self.db.commit()
            await self.db.refresh(old_token)

            new_refresh_value = f"{old_token.id}.{new_secret}"
            access_expiry = int(get_settings().jwt_expiry or 900)

            logger.info("Successfully rotated refresh token for user_id=%s", user.id)
            return AuthTokensResponse(
                access_token=access_token,
                refresh_token=new_refresh_value,
                token_type="Bearer",
                expires_in=access_expiry,
                refresh_expires_in=refresh_expiry,
                require_password_change=user.require_password_change,
            ), None

        except Exception as e:
            logger.error("Error in refresh_token service: %s", e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while refreshing token",
                status_code=500,
            )

    async def logout(
        self,
        user_id: Union[uuid.UUID, str],
    ):
        """Revoke all refresh tokens for the user."""
        try:
            logger.info("Revoking refresh tokens for logout user_id=%s", user_id)

            await self.db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == str(user_id))
                .values(revoked_at=datetime.now(dt_timezone.utc))
            )
            await self.db.commit()

            logger.info("Logged out user_id=%s", user_id)
            return success_response(
                message="Logged out successfully",
                status_code=200,
                success=True,
            ), None

        except Exception as e:
            logger.error("Error during logout service for user_id=%s: %s", user_id, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred during logout",
                status_code=500,
            )

    async def change_password(
        self,
        user_id: Union[uuid.UUID, str],
        old_password: str,
        new_password: str,
    ):
        try:
            logger.info("Changing password for user_id=%s", user_id)
            user = await self._get_user_by_id(user_id)

            if user is None:
                logger.warning("Change password failed: user_id=%s not found", user_id)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            if not bcrypt_verify(old_password, user.password_hash):
                logger.warning("Change password failed: old password mismatch for user_id=%s", user_id)
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Current password is incorrect",
                    status_code=400,
                )

            if not validate_password(new_password):
                logger.warning("Change password failed: invalid new password complexity for user_id=%s", user_id)
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    (
                        "Password must be at least 8 characters "
                        "long and include uppercase, lowercase, "
                        "number, and special character with no spaces."
                    ),
                    status_code=400,
                )

            new_hash, _ = bcrypt_hash(new_password)

            await self.db.execute(
                update(User)
                .where(User.id == str(user_id))
                .values(
                    password_hash=new_hash,
                    require_password_change=False,
                )
            )

            await self.db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == str(user_id))
                .values(revoked_at=datetime.now(dt_timezone.utc))
            )

            await self.db.commit()

            logger.info("Password updated and tokens revoked for user_id=%s", user_id)
            return success_response(
                message="Password Changed successfully",
                status_code=200,
                success=True,
            ), None

        except Exception as e:
            logger.error("Error in change_password service for user_id=%s: %s", user_id, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while changing password",
                status_code=500,
            )

    async def request_password_reset(
        self,
        email: str,
    ):
        try:
            clean_email = email.lower().strip()
            logger.info("Requesting password reset OTP for clean_email=%s", clean_email)

            user = await self._get_user_by_email(clean_email)

            if user is None:
                logger.warning("Password reset request failed: user email=%s not found", clean_email)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            otp = generate_pw_otp(6)
            expiry_minutes = int(get_settings().otp_expiry_minutes or 15)
            expires_at = datetime.now(dt_timezone.utc) + timedelta(minutes=expiry_minutes)
            otp_hash, _ = hash_password(otp)

            key = self._password_reset_otp_key(user.id)
            await self._invalidate_otp(key)
            await self._save_otp(
                key=key,
                user_id=user.id,
                otp_hash=otp_hash,
                expires_at=expires_at,
            )

            await send_password_reset_otp(
                user.email,
                otp,
            )

            logger.info("Password reset OTP sent to email=%s", clean_email)
            return success_response(
                message=(
                    "A password reset OTP has been sent "
                    "to your email address"
                ),
                status_code=200,
                success=True,
            ), None

        except Exception as e:
            logger.error("Error requesting password reset for email=%s: %s", email, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while requesting password reset",
                status_code=500,
            )

    async def reset_password(
        self,
        email: str,
        otp: str,
        new_password: str,
    ):
        try:
            clean_email = email.lower().strip()
            logger.info("Confirming password reset for email=%s", clean_email)

            user = await self._get_user_by_email(clean_email)

            if user is None:
                logger.warning("Reset password confirm failed: email=%s not found", clean_email)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            key = self._password_reset_otp_key(user.id)
            otp_record = await self._get_otp(key)

            if otp_record is None or not bcrypt_verify(otp, otp_record["otp_hash"]):
                logger.warning("Reset password confirm failed: invalid/expired OTP for email=%s", clean_email)
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Invalid or expired OTP",
                    status_code=400,
                )

            if not validate_password(new_password):
                logger.warning("Reset password confirm failed: weak new password for email=%s", clean_email)
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    (
                        "Password must be at least 8 characters "
                        "long and include uppercase, lowercase, "
                        "number, and special character with no spaces."
                    ),
                    status_code=400,
                )

            new_hash, _ = bcrypt_hash(new_password)

            await self.db.execute(
                update(User)
                .where(User.id == str(user.id))
                .values(
                    password_hash=new_hash,
                    require_password_change=False,
                )
            )

            await self.db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == str(user.id))
                .values(revoked_at=datetime.now(dt_timezone.utc))
            )

            await self.db.commit()

            otp_record["used_at"] = datetime.now(dt_timezone.utc).isoformat()
            await self.redis.set(
                key,
                json.dumps(otp_record),
                ex=60,
            )

            logger.info("Password successfully reset for user_id=%s", user.id)
            return success_response(
                message="Password reset completed",
                status_code=200,
                success=True,
            ), None

        except Exception as e:
            logger.error("Error resetting password for email=%s: %s", email, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while resetting password",
                status_code=500,
            )

    async def verify_email(self, email: str, otp: str, platform: str = "web"):
        try:
            clean_email = email.lower().strip()
            logger.info("Verifying email OTP for clean_email=%s", clean_email)

            temp_user_data = await self._get_temp_user(clean_email)

            if temp_user_data is None:
                logger.warning("Email verification failed: temp user session expired for email=%s", clean_email)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "Verification session has expired",
                    status_code=404,
                )

            user_id = str(temp_user_data["id"])
            otp_key = self._email_verification_otp_key(user_id)
            otp_record = await self._get_otp(otp_key)

            if otp_record is None or not bcrypt_verify(otp, otp_record["otp_hash"]):
                logger.warning("Email verification failed: invalid/expired OTP for email=%s", clean_email)
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "The provided OTP is invalid or expired",
                    status_code=400,
                )

            user_data = dict(temp_user_data)
            if user_data.get("id"):
                user_data["id"] = str(user_data["id"])
            if user_data.get("role_id"):
                user_data["role_id"] = str(user_data["role_id"])
            if user_data.get("organization_id"):
                user_data["organization_id"] = str(user_data["organization_id"])
            if user_data.get("created_at"):
                user_data["created_at"] = datetime.fromisoformat(user_data["created_at"])
            if user_data.get("joined_at"):
                user_data["joined_at"] = datetime.fromisoformat(user_data["joined_at"])

            user_data["is_verified"] = True
            user_data["is_active"] = True

            user = User(**user_data)
            self.db.add(user)
            await self.db.commit()

            user = await self._get_user_by_id(user.id)

            if user is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found after verification",
                    status_code=404,
                )

            role_name = self._normalize_role(user)
            organization_id = (
                str(user.organization_id)
                if user.organization_id
                else None
            )

            access_token, jwt_err = create_jwt(
                role=role_name,
                user_id=str(user.id),
                organization_id=organization_id,
                platform=platform,
            )

            if jwt_err:
                return None, error_response(
                    jwt_err.code,
                    jwt_err.message,
                    status_code=jwt_err.status_code,
                )

            refresh_secret = generate_refresh_secret_64hex()
            refresh_hash, _ = bcrypt_hash(refresh_secret)
            refresh_expiry = int(get_settings().refresh_token_expiry or 604800)
            expires_at = datetime.now(dt_timezone.utc) + timedelta(minutes=refresh_expiry)

            refresh_token = RefreshToken(
                id=str(uuid.uuid4()),
                user_id=str(user.id),
                token_hash=refresh_hash,
                expires_at=expires_at,
                revoked_at=None,
            )

            self.db.add(refresh_token)
            await self.db.commit()
            await self.db.refresh(refresh_token)

            refresh_token_value = f"{refresh_token.id}.{refresh_secret}"

            await self.redis.delete(self._user_redis_key(clean_email))
            await self.redis.delete(otp_key)

            access_expiry = int(get_settings().jwt_expiry or 900)

            logger.info("Email verification complete and user activated user_id=%s", user.id)
            return AuthTokensResponse(
                access_token=access_token,
                refresh_token=refresh_token_value,
                token_type="Bearer",
                expires_in=access_expiry,
                refresh_expires_in=refresh_expiry,
                require_password_change=user.require_password_change,
            ), None

        except Exception as e:
            logger.error("Error in verify_email service for email=%s: %s", email, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while verifying email",
                status_code=500,
            )

    async def resend_verification_otp(
        self,
        email: str,
    ):
        try:
            clean_email = email.lower().strip()
            logger.info("Resending verification OTP for clean_email=%s", clean_email)

            user = await self._get_user_by_email(clean_email)

            if user is None:
                logger.warning("Resend verification OTP failed: user email=%s not found", clean_email)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            if user.is_verified:
                logger.warning("Resend verification OTP failed: email=%s is already verified", clean_email)
                return None, error_response(
                    ErrorCode.ErrConflict,
                    "Email address is already verified",
                    status_code=409,
                )

            rate_key = self._email_resend_key(clean_email)
            last_sent = await self.redis.get(rate_key)

            if last_sent:
                if isinstance(last_sent, bytes):
                    last_sent = last_sent.decode("utf-8")

                last_sent_at = datetime.fromisoformat(last_sent)

                if (
                    datetime.now(dt_timezone.utc) - last_sent_at
                    < timedelta(minutes=1)
                ):
                    logger.warning("Resend verification OTP rate limited for email=%s", clean_email)
                    return None, error_response(
                        ErrorCode.ErrRateLimitExceeded,
                        (
                            "Please wait before requesting "
                            "another verification code"
                        ),
                        status_code=429,
                    )

            now = datetime.now(dt_timezone.utc)
            await self.redis.set(
                rate_key,
                now.isoformat(),
                ex=3600,
            )

            otp = generate_pw_otp(6)
            expiry_minutes = int(get_settings().otp_expiry_minutes or 15)
            expires_at = now + timedelta(minutes=expiry_minutes)
            otp_hash, _ = hash_password(otp)

            otp_key = self._email_verification_otp_key(user.id)
            await self._invalidate_otp(otp_key)
            await self._save_otp(
                key=otp_key,
                user_id=user.id,
                otp_hash=otp_hash,
                expires_at=expires_at,
            )

            await send_email_verification_otp(
                user.email,
                otp,
                expiry_minutes,
            )

            logger.info("Resent verification OTP for user_id=%s email=%s", user.id, clean_email)
            return success_response(
                message=(
                    "A new verification OTP has been sent "
                    "to your email address"
                ),
                status_code=200,
                success=True,
            ), None

        except Exception as e:
            logger.error("Error resending verification OTP for email=%s: %s", email, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while resending verification OTP",
                status_code=500,
            )

    async def update_user(
        self,
        user_id: Union[uuid.UUID, str],
        full_name: Optional[str] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        timezone: Optional[str] = None,
    ):
        try:
            logger.info("Executing update_user service for user_id=%s", user_id)
            user = await self._get_user_by_id(user_id)

            if user is None:
                logger.warning("Update user failed: user_id=%s not found", user_id)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            updates = {}

            if full_name is not None:
                if len(full_name) > 30:
                    logger.warning("Update user failed: full_name length exceeded for user_id=%s", user_id)
                    return None, error_response(
                        ErrorCode.ErrBadRequest,
                        "Full name must not exceed 30 characters",
                        status_code=400,
                    )
                updates["full_name"] = full_name

            if username is not None:
                if len(username) > 30:
                    logger.warning("Update user failed: username length exceeded for user_id=%s", user_id)
                    return None, error_response(
                        ErrorCode.ErrBadRequest,
                        "Username must not exceed 30 characters",
                        status_code=400,
                    )

                if username != user.username:
                    if await self._exists_by_username(username):
                        logger.warning("Update user failed: username=%s taken", username)
                        return None, error_response(
                            ErrorCode.ErrConflict,
                            "Username is already taken",
                            status_code=409,
                        )

                updates["username"] = username

            if avatar_url is not None:
                if isinstance(avatar_url, str) and avatar_url.startswith("data:image/"):
                    try:
                        import base64
                        from src.utils.storage import upload_s3_object

                        header, encoded = avatar_url.split(",", 1)
                        content_type = header.split(";")[0].replace("data:", "")
                        file_bytes = base64.b64decode(encoded)
                        ext = content_type.split("/")[-1] if "/" in content_type else "png"
                        storage_key = f"users/avatars/{user_id}/{uuid.uuid4()}.{ext}"
                        logger.info('before image upload')
                        uploaded_s3_url = upload_s3_object(file_bytes, storage_key, content_type)
                        updates["avatar_url"] = uploaded_s3_url
                        logger.info("Uploaded base64 avatar image to S3 for user_id=%s: %s", user_id, uploaded_s3_url)
                    except Exception as upload_exc:
                        logger.error("Failed to upload base64 avatar image to S3 for user_id=%s: %s", user_id, upload_exc, exc_info=True)
                        updates["avatar_url"] = avatar_url
                elif hasattr(avatar_url, "read") or hasattr(avatar_url, "getvalue"):
                    try:
                        from src.utils.storage import upload_logo

                        filename = getattr(avatar_url, "filename", "avatar.png")
                        content_type = getattr(avatar_url, "content_type", "image/png")
                        uploaded_s3_url, _ = upload_logo(avatar_url, f"user_{user_id}_{filename}", content_type)
                        updates["avatar_url"] = uploaded_s3_url
                        logger.info("Uploaded avatar file to S3 using storage.upload_logo for user_id=%s: %s", user_id, uploaded_s3_url)
                    except Exception as upload_exc:
                        logger.error("Failed to upload avatar file to S3 for user_id=%s: %s", user_id, upload_exc, exc_info=True)
                else:
                    updates["avatar_url"] = str(avatar_url)

            if timezone is not None:
                updates["timezone"] = timezone

            if not updates:
                logger.info("No profile changes provided for user_id=%s", user_id)
                return success_response(
                    message="No changes were made",
                    status_code=200,
                    success=True,
                ), None

            await self.db.execute(
                update(User)
                .where(User.id == str(user_id))
                .values(**updates)
            )

            await self.db.commit()
            logger.info("Successfully updated user profile in database for user_id=%s", user_id)

            return success_response(
                message="Updated profile successfully",
                status_code=200,
                success=True,
            ), None

        except Exception as e:
            logger.error("Error updating profile for user_id=%s: %s", user_id, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while updating profile",
                status_code=500,
            )

    async def get_user(self, user_id: Union[uuid.UUID, str]):
        try:
            logger.info("Fetching user profile for user_id=%s", user_id)
            user = await self._get_user_by_id(user_id)

            if user is None:
                logger.warning("User profile fetch failed: user_id=%s not found", user_id)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            return user, None

        except Exception as e:
            logger.error("Error fetching user profile for user_id=%s: %s", user_id, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while fetching user",
                status_code=500,
            )

    async def get_user_by_id(self, user_id: Union[uuid.UUID, str], organization_id: Union[uuid.UUID, str]):
        try:
            logger.info("Fetching target user_id=%s in org_id=%s", user_id, organization_id)

            if str(user_id) == str(uuid.UUID(int=0)):
                logger.warning("Get user by ID rejected: nil UUID user_id")
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Invalid user ID",
                    status_code=400,
                )

            user = await self._get_user_by_id(user_id)

            if user is None:
                logger.warning("Target user_id=%s not found", user_id)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            user_org_id = user.organization_id

            if not user_org_id or user_org_id == str(uuid.UUID(int=0)) or str(user_org_id) != str(organization_id):
                logger.warning("Org mismatch: user_org_id=%s req_org_id=%s", user_org_id, organization_id)
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    (
                        "You do not have permission "
                        "to perform this action"
                    ),
                    status_code=403,
                )

            return user, None

        except Exception as e:
            logger.error("Error fetching user_id=%s by org_id=%s: %s", user_id, organization_id, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while fetching user by ID",
                status_code=500,
            )

    async def is_email_available(self, email: str):
        try:
            clean_email = email.lower().strip()
            exists = await self._exists_by_email(clean_email)
            return not exists, None
        except Exception as e:
            logger.error("Error validating email availability for email=%s: %s", email, e, exc_info=True)
            return False, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred during email validation",
                status_code=500,
            )

    async def is_username_available(self, username: str):
        try:
            clean_username = username.strip()
            exists = await self._exists_by_username(clean_username)
            return not exists, None
        except Exception as e:
            logger.error("Error validating username availability for username=%s: %s", username, e, exc_info=True)
            return False, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred during username validation",
                status_code=500,
            )

    async def get_user_insights(
        self,
        user_id: Union[uuid.UUID, str],
    ):
        try:
            logger.info("Calculating user task insights for user_id=%s", user_id)
            user = await self._get_user_by_id(user_id)

            if user is None:
                logger.warning("User insights failed: user_id=%s not found", user_id)
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            if not user.organization_id:
                logger.warning("User insights failed: user_id=%s has no org context", user_id)
                return None, error_response(
                    ErrorCode.ErrValidation,
                    "User does not belong to any organization",
                    status_code=400,
                )

            organization_id = user.organization_id

            stmt = (
                select(
                    func.count(Task.id).label("total_assigned"),
                    func.count(Task.id)
                    .filter(CustomStatus.is_final.is_not(True))
                    .label("in_progress"),
                    func.count(Task.id)
                    .filter(CustomStatus.is_final.is_(True))
                    .label("completed"),
                )
                .select_from(Task)
                .join(Project, Project.id == Task.project_id)
                .join(CustomStatus, CustomStatus.id == Task.status_id)
                .where(
                    Project.organization_id == str(organization_id),
                    Task.assignee_id == str(user_id),
                    Task.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                    CustomStatus.deleted_at.is_(None),
                )
            )

            result = await self.db.execute(stmt)
            row = result.first()

            total = row.total_assigned if row else 0
            in_progress = row.in_progress if row else 0
            completed = row.completed if row else 0

            completion_percentage = (
                round((completed / total) * 100)
                if total > 0
                else 0.0
            )

            logger.info("Calculated insights for user_id=%s: total=%d in_progress=%d completed=%d", user_id, total, in_progress, completed)
            return {
                "total_assigned": total,
                "in_progress": in_progress,
                "completed": completed,
                "completion_percentage": completion_percentage,
            }, None

        except Exception as e:
            logger.error("Error calculating user insights for user_id=%s: %s", user_id, e, exc_info=True)
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                str(e) or "An unexpected error occurred while calculating user insights",
                status_code=500,
            )