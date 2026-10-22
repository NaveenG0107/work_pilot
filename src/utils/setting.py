# src/utils/setting.py
from __future__ import annotations

from functools import lru_cache
import os
from typing import Optional, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -----------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------
    db_host: str = Field(default="localhost", env="DB_HOST")
    db_port: int = Field(default=5432, env="DB_PORT")
    db_username: str = Field(default="", env="DB_USERNAME")
    db_password: str = Field(default="", env="DB_PASSWORD")
    db_name: str = Field(default="kanban", env="DB_NAME")
    db_ssl_mode: str = Field(default="require", env="DB_SSL_MODE")
    db_automigrate: bool = Field(default=False, env="DB_AUTOMIGRATE")
    db_prefer_simple_protocol: bool = Field(default=False, env="DB_PREFER_SIMPLE_PROTOCOL")

    # -----------------------------------------------------------------
    # HTTP
    # -----------------------------------------------------------------
    http_port: int = Field(default=6369, env="HTTP_PORT")

    # -----------------------------------------------------------------
    # Logger
    # -----------------------------------------------------------------
    logger_type: str = Field(default="development", env="LOGGER_TYPE")
    logger_level: str = Field(default="debug", env="LOGGER_LEVEL")

    # -----------------------------------------------------------------
    # Redis
    # -----------------------------------------------------------------
    redis_host: str = Field(default="localhost", env="REDIS_HOST")
    redis_port: int = Field(default=6379, env="REDIS_PORT")
    redis_password: str = Field(default="", env="REDIS_PASSWORD")

    # -----------------------------------------------------------------
    # JWT & Authentication (Single Source of Truth from Environment)
    # -----------------------------------------------------------------
    jwt_secret_key: str = Field(..., env="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", env="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: Optional[int] = Field(default=None, env="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: Optional[int] = Field(default=None, env="JWT_REFRESH_TOKEN_EXPIRE_DAYS")
    jwt_expiry: Optional[int] = Field(default=None, env="JWT_EXPIRY")  # in seconds
    refresh_token_expiry: Optional[int] = Field(default=None, env="REFRESH_TOKEN_EXPIRY")  # in seconds
    jwt_sliding_session_enabled: bool = True
    jwt_renewal_interval: int = Field(default=60, gt=0)  # seconds between renewals
    otp_expiry_minutes: int = Field(default=15, env="OTP_EXPIRY_MINUTES")

    @model_validator(mode="after")
    def resolve_token_expiries(self) -> Self:
        """Resolve token expirations dynamically from environment variables without hardcoded discrepancies."""
        # 1. Access token expiration in seconds:
        # Prioritize JWT_EXPIRY if explicitly provided, else compute from JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        if self.jwt_expiry is not None:
            self.jwt_expiry = int(self.jwt_expiry)
            if self.jwt_access_token_expire_minutes is None:
                self.jwt_access_token_expire_minutes = max(1, self.jwt_expiry // 60)
        elif self.jwt_access_token_expire_minutes is not None:
            self.jwt_expiry = int(self.jwt_access_token_expire_minutes * 60)
        else:
            self.jwt_expiry = 900
            self.jwt_access_token_expire_minutes = 15

        # 2. Refresh token expiration in seconds:
        # Prioritize REFRESH_TOKEN_EXPIRY if provided, else compute from JWT_REFRESH_TOKEN_EXPIRE_DAYS
        if self.refresh_token_expiry is not None:
            self.refresh_token_expiry = int(self.refresh_token_expiry)
            if self.jwt_refresh_token_expire_days is None:
                self.jwt_refresh_token_expire_days = max(1, self.refresh_token_expiry // 86400)
        elif self.jwt_refresh_token_expire_days is not None:
            self.refresh_token_expiry = int(self.jwt_refresh_token_expire_days * 86400)
        else:
            self.refresh_token_expiry = 604800
            self.jwt_refresh_token_expire_days = 7

        return self

    # -----------------------------------------------------------------
    # Email (Brevo primary / Resend fallback)
    # -----------------------------------------------------------------
    brevo_api_key: Optional[str] = Field(default="", env="BREVO_API_KEY")
    brevo_from_email: Optional[str] = Field(default="", env="BREVO_FROM_EMAIL")
    resend_api_key: Optional[str] = Field(default="", env="RESEND_API_KEY")
    resend_from_email: Optional[str] = Field(default="", env="RESEND_FROM_EMAIL")

    # -----------------------------------------------------------------
    # Frontend / Backend URLs
    # -----------------------------------------------------------------
    frontend_dashboard_url: str = Field(default="http://localhost:3000", env="FRONTEND_DASHBOARD_URL")
    backend_api_url: str = Field(default="http://localhost:8000", env="BACKEND_API_URL")
    cors_origins: str = Field(default="*", env="CORS_ORIGINS")

    # -----------------------------------------------------------------
    # Cookie
    # -----------------------------------------------------------------
    cookie_secure: bool = Field(default=False, env="COOKIE_SECURE")
    cookie_domain: Optional[str] = Field(default=None, env="COOKIE_DOMAIN")
    cookie_path: str = Field(default="/", env="COOKIE_PATH")
    cookie_samesite: str = Field(default="lax", env="COOKIE_SAMESITE")

    # -----------------------------------------------------------------
    # S3 (Supabase-compatible storage)
    # -----------------------------------------------------------------
    s3_endpoint: str = Field(default="", env="S3_ENDPOINT")
    s3_public_endpoint: str = Field(default="", env="S3_PUBLIC_ENDPOINT")
    s3_region: str = Field(default="ap-south-1", env="S3_REGION")
    s3_access_key_id: str = Field(default="", env="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="", env="S3_SECRET_ACCESS_KEY")
    s3_bucket: str = Field(default="work_pilot_bucket", env="S3_BUCKET")
    s3_max_file_size_mb: int = Field(default=5, env="S3_MAX_FILE_SIZE_MB")

    # -----------------------------------------------------------------
    # Attachment configs
    # -----------------------------------------------------------------
    attachment_max_file_size_mb: int = Field(default=10, env="ATTACHMENT_MAX_FILE_SIZE_MB")
    attachment_max_files_count: int = Field(default=5, env="ATTACHMENT_MAX_FILES_COUNT")

    # -----------------------------------------------------------------
    # Derived / helper properties
    # -----------------------------------------------------------------
    @property
    def db_url(self) -> str:
        """Postgres DSN (sync psycopg2 compatible)."""
        return (
            f"postgresql+psycopg2://{self.db_username}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?sslmode={self.db_ssl_mode}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton to get the populated Settings instance."""
    return Settings()
