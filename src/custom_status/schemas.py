# src/custom_status/schemas.py
import re
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator

HEX_COLOR_REGEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class CreateCustomStatusRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="Status name")
    color: str = Field(..., description="Status color in hexadecimal (#RRGGBB)")
    display_order: int = Field(default=0, ge=0, description="Display order")
    is_final: Optional[bool] = Field(default=False, description="Whether status is a final/completed status")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 50:
            raise ValueError("Status name must be between 1 and 50 characters")
        return v

    @field_validator("color")
    @classmethod
    def validate_color(cls, v: str) -> str:
        v = v.strip()
        if not HEX_COLOR_REGEX.match(v):
            raise ValueError("Status color must be a valid hexadecimal color code (#RRGGBB)")
        return v

    @field_validator("display_order")
    @classmethod
    def validate_display_order(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Display order must be greater than or equal to 0")
        return v


class UpdateCustomStatusRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    color: Optional[str] = Field(None)
    display_order: Optional[int] = Field(None, ge=0)
    is_final: Optional[bool] = Field(None)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v or len(v) > 50:
                raise ValueError("Status name must be between 1 and 50 characters")
        return v

    @field_validator("color")
    @classmethod
    def validate_color(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not HEX_COLOR_REGEX.match(v):
                raise ValueError("Status color must be a valid hexadecimal color code (#RRGGBB)")
        return v

    @field_validator("display_order")
    @classmethod
    def validate_display_order(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("Display order must be greater than or equal to 0")
        return v


class CustomStatusResponse(BaseModel):
    id: Optional[UUID] = Field(None, description="Custom Status UUID")
    project_id: UUID = Field(..., description="Project UUID")
    name: str = Field(..., description="Status name")
    color: str = Field(..., description="Status color hex code")
    display_order: int = Field(..., description="Display order sequence")
    is_default: bool = Field(default=False, description="Whether this is a default status")
    is_final: bool = Field(default=False, description="Whether this is a final completed status")

    class Config:
        from_attributes = True
