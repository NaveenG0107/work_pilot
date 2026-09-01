from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
import re


HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


class CreateUserStoryStatusRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    color: str
    display_order: int = Field(..., ge=0)
    is_closed: Optional[bool] = None
    is_final: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "Status name must be between 1 and 50 characters"
            )

        if len(value) > 50:
            raise ValueError(
                "Status name must be between 1 and 50 characters"
            )

        return value

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        if not HEX_COLOR_PATTERN.match(value):
            raise ValueError(
                "Status color must be a valid hexadecimal color code (#RRGGBB)"
            )

        return value


class UpdateUserStoryStatusRequest(BaseModel):
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    color: Optional[str] = None
    display_order: Optional[int] = Field(default=None, ge=0)
    is_closed: Optional[bool] = None
    is_final: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = value.strip()

        if not value:
            raise ValueError(
                "Status name must be between 1 and 50 characters"
            )

        if len(value) > 50:
            raise ValueError(
                "Status name must be between 1 and 50 characters"
            )

        return value

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        if not HEX_COLOR_PATTERN.match(value):
            raise ValueError(
                "Status color must be a valid hexadecimal color code (#RRGGBB)"
            )

        return value


class UserStoryStatusResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    color: str
    display_order: int
    is_default: bool
    is_closed: bool
    is_final: bool

    class Config:
        from_attributes = True


class SuccessResponse(BaseModel):
    success: bool
    status_code: int
    message: str
    data: object


class ErrorResponse(BaseModel):
    success: bool = False
    code: str
    message: str