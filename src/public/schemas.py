from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class CountryResponse(BaseModel):
    id: str
    name: str
    iso2: str
    iso3: str
    phone_code: str | None = None
    timezone: list[str]
    flag_emoji: str | None = None
    created_at: datetime
    updated_at: datetime

    # Cached response objects are shared between requests and must not mutate.
    model_config = ConfigDict(from_attributes=True, frozen=True)

    @field_validator("phone_code", "flag_emoji", mode="before")
    @classmethod
    def serialize_nullable_strings_like_go(cls, value: str | None) -> str:
        # These fields are non-pointer strings in the Go response model.
        return value or ""


class CountriesResponse(BaseModel):
    success: bool
    status_code: int
    message: str
    data: list[CountryResponse]


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str


class HealthDependencies(BaseModel):
    database: str


class FullHealthResponse(HealthResponse):
    dependencies: HealthDependencies
