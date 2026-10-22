"""
WorkPilot Utilities Package.
Provides centralized, organized helpers for response envelopes, JWT authentication,
database seeding, password handling, and security.
"""

from src.utils.core import (
    APIError,
    ErrorCode,
    create_jwt,
    error_response,
    require_jwt,
    success_response,
    verify_jwt,
)
from src.utils.jwt_handler import (
    PLATFORM_TOKEN_LIFETIMES,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from src.utils.password_helper import (
    bcrypt_hash,
    bcrypt_verify,
    hash_password,
    validate_password,
    verify_password,
)
from src.utils.response import ERROR_CODES, error, success
from src.utils.seeder import (
    DEFAULT_PERMISSIONS,
    run_seeders,
    seed_countries,
    seed_permissions,
    seed_super_admin,
)

__all__ = [
    # Response
    "APIError",
    "ErrorCode",
    "ERROR_CODES",
    "error",
    "error_response",
    "success",
    "success_response",
    # JWT
    "PLATFORM_TOKEN_LIFETIMES",
    "create_access_token",
    "create_jwt",
    "create_refresh_token",
    "decode_token",
    "require_jwt",
    "verify_jwt",
    # Passwords
    "bcrypt_hash",
    "bcrypt_verify",
    "hash_password",
    "validate_password",
    "verify_password",
    # Seeder
    "DEFAULT_PERMISSIONS",
    "run_seeders",
    "seed_countries",
    "seed_permissions",
    "seed_super_admin",
]
