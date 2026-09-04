# src/organization/schemas.py
"""
Pydantic schemas for the organization module.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class Industry(str, Enum):
    INFORMATION_TECHNOLOGY = "Information_Technology"
    FINANCE = "Finance"
    HEALTHCARE = "Healthcare"
    EDUCATION = "Education"
    MANUFACTURING = "Manufacturing"
    RETAIL = "Retail"
    REAL_ESTATE = "Real Estate"
    LOGISTICS = "Logistics"
    HOSPITALITY = "Hospitality"
    OTHER = "Other"


class TeamSize(str, Enum):
    SIZE_1_10 = "1-10"
    SIZE_11_50 = "11-50"
    SIZE_51_200 = "51-200"
    SIZE_201_500 = "201-500"
    SIZE_501_1000 = "501-1000"
    SIZE_1000_PLUS = "1000+"


# ---------------------------------------------------------------- requests


class CreateOrganizationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    domain: str = Field(..., min_length=1, max_length=150)
    industry: Industry
    team_size: TeamSize
    country_id: UUID


class UpdateOrganizationRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=50)
    domain: Optional[str] = Field(None, max_length=150)
    team_size: Optional[TeamSize] = None


class UpdateOrganizationStatusRequest(BaseModel):
    is_active: bool


class UserStatusRequest(BaseModel):
    is_active: bool
    user_id: UUID


class UserRoleRequest(BaseModel):
    role: str
    user_id: UUID


class RemoveUserRequest(BaseModel):
    user_id: UUID


class InviteOrganizationMemberItem(BaseModel):
    email: str = Field(..., min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        value = v.strip().lower()
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Invalid email address")
        return value


class InviteOrganizationMemberRequest(BaseModel):
    members: List[InviteOrganizationMemberItem] = Field(..., min_length=1)


class AcceptInvitationRequest(BaseModel):
    token: str = Field(..., min_length=1)


# ---------------------------------------------------------------- filter/query


class PaginationQuery(BaseModel):
    page: int = 1
    page_size: int = 10


class OrganizationFilterRequest(BaseModel):
    page: int = 1
    page_size: int = 10
    sort_by: str = ""
    sort_order: str = ""
    name: str = ""
    domain: str = ""
    industry: str = ""
    team_size: str = ""
    country: str = ""
    is_active: Optional[bool] = None
    search: str = ""


class OrganizationMemberListFilter(BaseModel):
    page: int = 1
    page_size: int = 10
    full_name: str = ""
    email: str = ""
    username: str = ""
    role: str = ""
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    status: str = ""
    timezone: str = ""
    include_org_admins: bool = False


class GlobalMemberListFilter(BaseModel):
    page: int = 1
    page_size: int = 10
    sort_by: str = ""
    sort_order: str = ""
    search: str = ""
    full_name: str = ""
    email: str = ""
    username: str = ""
    role: str = ""
    organization_id: Optional[UUID] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    timezone: str = ""


# ---------------------------------------------------------------- responses


class Pagination(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class OrganizationSummary(BaseModel):
    id: UUID
    name: str
    slug: Optional[str] = None
    domain: str
    industry: Optional[str] = None
    team_size: Optional[str] = None
    country: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool
    created_at: datetime
    total_projects: int = 0
    total_members: int = 0


class UserProfile(BaseModel):
    id: UUID
    organization_id: Optional[UUID] = None
    organization_name: Optional[str] = None
    name: str
    username: str
    email: str
    role: Optional[str] = None
    avatar_url: Optional[str] = None
    color: str
    timezone: Optional[str] = None
    is_active: bool
    is_verified: bool
    status: str
    created_at: datetime
    joined_at: Optional[datetime] = None
    require_password_change: bool
    total_assigned: Optional[int] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    in_progress: Optional[int] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    completed: Optional[int] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    completion_percentage: Optional[float] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class AuthTokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_expires_in: int
    require_password_change: bool = False


class SimpleSuccess(BaseModel):
    success: bool = True


# ------------------------------------------------------------------ roles


class CreateRoleRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    permissions: Dict[str, Dict[str, bool]] = Field(..., min_length=1)


class UpdateRoleRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    permissions: Optional[Dict[str, Dict[str, bool]]] = None


class RoleResponse(BaseModel):
    """
    Role response with permissions map.
    """

    id: UUID
    organization_id: Optional[UUID] = None
    name: str
    description: Optional[str] = None
    is_system: bool
    permissions: Dict[str, Dict[str, bool]]
    created_at: datetime
    updated_at: Optional[datetime] = None
