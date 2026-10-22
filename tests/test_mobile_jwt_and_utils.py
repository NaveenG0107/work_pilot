import json
from datetime import datetime, timezone
import pytest
import jwt

from src.utils.jwt_handler import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from src.utils.core import create_jwt, verify_jwt
from src.utils.response import success as util_success, error as util_error, ERROR_CODES
import src.utils.seeder as utils_seeder


def test_mobile_access_token_has_no_exp_claim():
    """Verify that JWT access tokens for mobile do not include an 'exp' claim."""
    user_id = "test-user-123"
    email = "mobile_user@example.com"
    role = "member"

    # 1. From jwt_handler.create_access_token
    token = create_access_token(
        user_id=user_id,
        email=email,
        role=role,
        platform="mobile",
    )
    unverified = jwt.decode(token, options={"verify_signature": False})
    assert "exp" not in unverified, f"Expected no 'exp' claim in mobile token, got: {unverified.get('exp')}"
    assert unverified["sub"] == user_id
    assert unverified["platform"] == "mobile"
    assert unverified["type"] == "access"

    # Verify decode_token works on token without exp
    decoded = decode_token(token)
    assert decoded["sub"] == user_id
    assert "exp" not in decoded


def test_mobile_refresh_token_has_no_exp_claim():
    """Verify that JWT refresh tokens for mobile do not include an 'exp' claim."""
    user_id = "test-user-456"
    email = "mobile_user2@example.com"
    role = "member"

    token = create_refresh_token(
        user_id=user_id,
        email=email,
        role=role,
        platform="mobile",
    )
    unverified = jwt.decode(token, options={"verify_signature": False})
    assert "exp" not in unverified, f"Expected no 'exp' claim in mobile refresh token, got: {unverified.get('exp')}"
    assert unverified["sub"] == user_id
    assert unverified["platform"] == "mobile"
    assert unverified["type"] == "refresh"


def test_web_access_token_has_exp_claim():
    """Verify that JWT access tokens for web DO include an 'exp' claim."""
    user_id = "web-user-123"
    email = "web_user@example.com"
    role = "member"

    token = create_access_token(
        user_id=user_id,
        email=email,
        role=role,
        platform="web",
    )
    unverified = jwt.decode(token, options={"verify_signature": False})
    assert "exp" in unverified, "Expected 'exp' claim in web token"
    assert unverified["exp"] > unverified["iat"]


def test_core_create_jwt_mobile_vs_web():
    """Verify that core.create_jwt adheres to platform expiry logic."""
    # Mobile
    mobile_token, err = create_jwt(
        role="member",
        user_id="user-mob-789",
        platform="mobile",
    )
    assert err is None
    unverified_mob = jwt.decode(mobile_token, options={"verify_signature": False})
    assert "exp" not in unverified_mob, "core.create_jwt mobile token must not have 'exp' claim"
    assert unverified_mob["sub"] == "user-mob-789"
    assert unverified_mob["user_id"] == "user-mob-789"

    # Can be verified with verify_jwt
    claims, err = verify_jwt(mobile_token)
    assert err is None
    assert claims is not None
    assert claims["user_id"] == "user-mob-789"

    # Web
    web_token, err = create_jwt(
        role="member",
        user_id="user-web-789",
        platform="web",
    )
    assert err is None
    unverified_web = jwt.decode(web_token, options={"verify_signature": False})
    assert "exp" in unverified_web, "core.create_jwt web token must have 'exp' claim"


def test_utils_response_helpers():
    """Verify that src.utils.response functions produce valid responses."""
    # Success response
    resp = util_success(message="Operation successful", data={"key": "val"}, status_code=200)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["success"] is True
    assert body["status_code"] == 200
    assert body["message"] == "Operation successful"
    assert body["data"] == {"key": "val"}

    # Error response
    err_resp = util_error(message="Not found", status_code=404)
    assert err_resp.status_code == 404
    err_body = json.loads(err_resp.body)
    assert err_body["success"] is False
    assert err_body["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert err_body["error"]["message"] == "Not found"


def test_utils_seeder_exports():
    """Verify that src.utils.seeder exports all required functions and constants."""
    assert len(utils_seeder.DEFAULT_PERMISSIONS) > 0
    assert callable(utils_seeder.seed_permissions)
    assert callable(utils_seeder.seed_countries)
    assert callable(utils_seeder.seed_super_admin)
    assert callable(utils_seeder.run_seeders)
    assert callable(utils_seeder.main)


def test_password_helper_functions():
    """Verify password hashing, verification, and policy validation."""
    from src.utils.password_helper import (
        bcrypt_hash,
        bcrypt_verify,
        hash_password,
        validate_password,
        verify_password,
    )

    raw_pw = "SuperSecurePassword123!"
    assert validate_password(raw_pw) is True
    assert validate_password("short") is False
    assert validate_password("NoSpecial123") is False

    # Argon2 / pwdlib
    hashed = hash_password(raw_pw)
    assert verify_password(raw_pw, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False

    # Bcrypt
    b_hashed, err = bcrypt_hash(raw_pw)
    assert err is None
    assert bcrypt_verify(raw_pw, b_hashed) is True
    assert bcrypt_verify("WrongPassword!", b_hashed) is False
    # Cross-verify using verify_password fallback
    assert verify_password(raw_pw, b_hashed) is True


def test_action_confirmation_schema():
    """Verify ActionConfirmationRequest handles confirmed and confirm fields."""
    from src.integrations.orchestration.schemas import ActionConfirmationRequest

    # Default confirmed = True
    req1 = ActionConfirmationRequest()
    assert req1.is_confirmed is True

    # confirmed = False
    req2 = ActionConfirmationRequest(confirmed=False)
    assert req2.is_confirmed is False

    # confirm = True
    req3 = ActionConfirmationRequest(confirm=True)
    assert req3.is_confirmed is True

    # confirm = False
    req4 = ActionConfirmationRequest(confirm=False)
    assert req4.is_confirmed is False


def test_dynamic_env_token_expiry():
    """Verify that settings dynamically resolves token expiries from environment."""
    from src.utils.setting import get_settings

    settings = get_settings()
    # In .env: JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1 -> jwt_expiry must be 60 seconds
    assert settings.jwt_access_token_expire_minutes == 1
    assert settings.jwt_expiry == 60

    # In .env: JWT_REFRESH_TOKEN_EXPIRE_DAYS=7 -> refresh_token_expiry must be 604800 seconds
    assert settings.jwt_refresh_token_expire_days == 7
    assert settings.refresh_token_expiry == 604800


