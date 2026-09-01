import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


HEX_COLOR_REGEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class CreateLabelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=30, description="Label name")
    color: str = Field(..., description="Hexadecimal color code (#RRGGBB)")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("Label name must be between 1 and 30 characters")
        if len(v) > 30:
            raise ValueError("Label name must be between 1 and 30 characters")
        return v

    @field_validator("color")
    @classmethod
    def validate_color(cls, v: str) -> str:
        v = v.strip()
        if not HEX_COLOR_REGEX.match(v):
            raise ValueError("Label color must be a valid hexadecimal color code (#RRGGBB)")
        return v


class UpdateLabelRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=30)
    color: Optional[str] = Field(None)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip().lower()
            if not v or len(v) > 30:
                raise ValueError("Label name must be between 1 and 30 characters")
        return v

    @field_validator("color")
    @classmethod
    def validate_color(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not HEX_COLOR_REGEX.match(v):
                raise ValueError("Label color must be a valid hexadecimal color code (#RRGGBB)")
        return v


class LabelResponse(BaseModel):
    id: str
    name: str
    color: str

    class Config:
        from_attributes = True
