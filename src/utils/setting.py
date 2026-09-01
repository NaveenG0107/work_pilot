# src/core/config.py
from __future__ import annotations

import os
from typing import Optional

from pydantic import Field, validator
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
    # JWT
    # -----------------------------------------------------------------
    jwt_secret_key: str = Field(..., env="JWT_SECRET_KEY")  # required, no default
    jwt_expiry: int = Field(default=90000, env="JWT_EXPIRY")  # seconds, default 15 min
    refresh_token_expiry: int = Field(default=604800, env="REFRESH_TOKEN_EXPIRY")  # seconds, default 7 days
    otp_expiry_minutes: int = Field(default=15, env="OTP_EXPIRY_MINUTES")  # minutes

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
    backend_api_url: str = Field(default="http://localhost:6369", env="BACKEND_API_URL")

    # -----------------------------------------------------------------
    # Cookie
    # -----------------------------------------------------------------
    cookie_secure: bool = Field(default=False, env="COOKIE_SECURE")
    cookie_domain: Optional[str] = Field(default=None, env="COOKIE_DOMAIN")
    cookie_path: str = Field(default="/", env="COOKIE_PATH")
    cookie_samesite: str = Field(default="lax", env="COOKIE_SAMESITE")

    # -----------------------------------------------------------------
    # S3 (Supabase‑compatible storage)
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


def get_settings() -> Settings:
    """Fast singleton to get the populated Settings instance."""
    return Settings()