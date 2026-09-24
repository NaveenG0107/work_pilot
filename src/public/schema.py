from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator  # type: ignore


class CountryResponse(BaseModel):
    id: str
    name: str
    iso2: str
    iso3: str
    phone_code: str = ""
    timezone: list[str]
    flag_emoji: str = ""
    created_at: datetime
    updated_at: datetime

    # Cached response objects are shared between requests and must not mutate.
    model_config = ConfigDict(from_attributes=True, frozen=True)

    @field_validator("phone_code", "flag_emoji", mode="before")
    @classmethod
    def serialize_nullable_strings_like_go(cls, value: str | None) -> str:
        # These fields are non-pointer strings in the Go response model.
        return value or ""

    @field_validator("updated_at", mode="before")
    @classmethod
    def serialize_null_time_like_go(cls, value: datetime | None) -> datetime:
        return value or datetime.min.replace(tzinfo=timezone.utc)

class CountriesResponse(BaseModel):
    success: bool
    status_code: int
    message: str
    data: list[CountryResponse]


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str


class HealthDependencies(BaseModel):
    database: str
    redis: str


class FullHealthResponse(BaseModel):
    dependencies: HealthDependencies
    status: str
    timestamp: str
    version: str
