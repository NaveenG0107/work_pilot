from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ============================================================
# ENUMS
# ============================================================

class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    ORG_ADMIN = "org_admin"
    MEMBER = "member"


class Platform(str, Enum):
    WEB = "web"
    MOBILE = "mobile"


# ============================================================
# RESPONSE
# ============================================================

class AuthTokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int
    require_password_change: bool


class AuthTokenSuccessResponse(BaseModel):
    success: bool
    status_code: int
    message: str
    data: AuthTokensResponse


class AuthSuccessResponse(BaseModel):
    success: bool
    status_code: int
    message: str


class UserIDResponse(BaseModel):
    userID: UUID


class UpdateUserSuccessResponse(AuthSuccessResponse):
    data: UserIDResponse


# ============================================================
# SIGN IN
# ============================================================

class SignInRequest(BaseModel):
    email: EmailStr
    password: str


# ============================================================
# REFRESH TOKEN
# ============================================================

class RefreshTokenRequest(BaseModel):
    refresh_token: str


# ============================================================
# SIGN UP
# ============================================================

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    username: str
    avatar_url: str | None = None
    timezone: str | None = None


# ============================================================
# VERIFY EMAIL
# ============================================================

class VerifyEmailRequest(BaseModel):
    email: EmailStr
    otp: str


# ============================================================
# RESEND VERIFICATION OTP
# ============================================================

class ResendVerificationOTPRequest(BaseModel):
    email: EmailStr


# ============================================================
# PASSWORD RESET REQUEST
# ============================================================

class PasswordResetRequest(BaseModel):
    email: EmailStr


# ============================================================
# PASSWORD RESET CONFIRM
# ============================================================

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str


# ============================================================
# CHANGE PASSWORD
# ============================================================

class ChangePasswordRequest(BaseModel):
    # Populated from JWT
    user_id: UUID | None = None

    old_password: str
    new_password: str


# ============================================================
# UPDATE USER
# ============================================================

class UpdateUserRequest(BaseModel):
    full_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    timezone: str | None = None


class OrganizationSummary(BaseModel):
    id: UUID
    name: str
    slug: str | None = None
    domain: str
    industry: str | None = None
    team_size: str | None = None
    country: str | None = None
    logo_url: str | None = None
    is_active: bool
    created_at: datetime
    total_projects: int
    total_members: int


class UserProfile(BaseModel):
    id: UUID
    organization_id: UUID | None = None
    organization_name: str | None = None
    name: str
    username: str
    email: str
    role: str | None = None
    avatar_url: str | None = None
    color: str
    timezone: str | None = None
    is_active: bool
    is_verified: bool
    status: str
    created_at: datetime
    joined_at: datetime | None = None
    require_password_change: bool

    model_config = ConfigDict(from_attributes=True)


class ProjectSummary(BaseModel):
    id: UUID
    organization_id: UUID
    organization_name: str | None = None
    name: str
    key: str | None = None
    project_key: str | None = None
    description: str | None = None
    status: str
    created_by: UUID
    created_at: datetime
    sprint_count: int
    total_tasks: int
    total_members: int
    sprints: list["Sprint"] | None = None


class ProjectMember(BaseModel):
    user_id: UUID
    username: str
    full_name: str
    role: str
    avatar_url: str | None = None
    color: str
    organization_name: str | None = None
    project_key: str | None = None


class Sprint(BaseModel):
    id: UUID
    name: str
    goal: str | None = None
    status: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    tasks: list["TaskResponse"] | None = None


class ProjectActivity(BaseModel):
    id: UUID
    project_id: UUID | None = None
    organization_id: UUID | None = None
    user: "UserSummary | None" = None
    action: str
    resource_type: str
    resource_id: str | None = None
    details: str | None = None
    timestamp: str
    task_key: str | None = None
    title: str | None = None


class UserSummary(BaseModel):
    id: UUID
    full_name: str
    email: str
    avatar_url: str | None = None
    color: str
    role: str | None = None


class UserTaskInsightsResponse(BaseModel):
    total_assigned: int
    in_progress: int
    completed: int
    completion_percentage: float
