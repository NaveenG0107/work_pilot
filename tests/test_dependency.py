from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.services.jwt_auth.dependancy import get_current_user, require_role


class TestGetCurrentUser:
    @patch("src.services.jwt_auth.dependancy.is_token_blacklisted")
    @patch("src.services.jwt_auth.dependancy.decode_token")
    def test_valid_access_token(self, mock_decode, mock_blacklist):
        mock_decode.return_value = {
            "sub": "user-id",
            "email": "test@example.com",
            "role": "user",
            "type": "access",
            "jti": "abc-123",
        }
        mock_blacklist.return_value = False

        credentials = MagicMock()
        credentials.credentials = "valid.token"
        mock_db = MagicMock()

        result = get_current_user(credentials, mock_db)

        assert result["sub"] == "user-id"
        assert result["email"] == "test@example.com"
        assert result["role"] == "user"

    @patch("src.services.jwt_auth.dependancy.decode_token")
    def test_invalid_token_raises_401(self, mock_decode):
        mock_decode.side_effect = Exception("Invalid token")

        credentials = MagicMock()
        credentials.credentials = "bad.token"
        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            get_current_user(credentials, mock_db)

        assert exc_info.value.status_code == 401
        assert "Invalid or expired token" in exc_info.value.detail

    @patch("src.services.jwt_auth.dependancy.decode_token")
    def test_refresh_token_type_raises_401(self, mock_decode):
        mock_decode.return_value = {
            "sub": "user-id",
            "email": "test@example.com",
            "role": "user",
            "type": "refresh",
        }

        credentials = MagicMock()
        credentials.credentials = "refresh.token"
        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            get_current_user(credentials, mock_db)

        assert exc_info.value.status_code == 401
        assert "Access token required" in exc_info.value.detail

    @patch("src.services.jwt_auth.dependancy.is_token_blacklisted")
    @patch("src.services.jwt_auth.dependancy.decode_token")
    def test_blacklisted_token_raises_401(self, mock_decode, mock_blacklist):
        mock_decode.return_value = {
            "sub": "user-id",
            "email": "test@example.com",
            "role": "user",
            "type": "access",
            "jti": "blacklisted-jti",
        }
        mock_blacklist.return_value = True

        credentials = MagicMock()
        credentials.credentials = "blacklisted.token"
        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            get_current_user(credentials, mock_db)

        assert exc_info.value.status_code == 401
        assert "Token has been revoked" in exc_info.value.detail


class TestRequireRole:
    @patch("src.services.jwt_auth.dependancy.get_current_user")
    def test_allowed_role_passes(self, mock_get_user):
        mock_get_user.return_value = {
            "sub": "user-id",
            "email": "admin@example.com",
            "role": "admin",
        }

        dependency = require_role("admin")
        result = dependency(current_user=mock_get_user.return_value)

        assert result["role"] == "admin"

    def test_disallowed_role_raises_403(self):
        dependency = require_role("admin")

        with pytest.raises(HTTPException) as exc_info:
            dependency(current_user={
                "sub": "user-id",
                "email": "user@example.com",
                "role": "user",
            })

        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail

    def test_multiple_allowed_roles(self):
        dependency = require_role("admin", "manager")

        result = dependency(current_user={
            "sub": "user-id",
            "email": "manager@example.com",
            "role": "manager",
        })

        assert result["role"] == "manager"

    def test_no_matching_role_raises_403(self):
        dependency = require_role("admin", "manager")

        with pytest.raises(HTTPException) as exc_info:
            dependency(current_user={
                "sub": "user-id",
                "email": "user@example.com",
                "role": "user",
            })

        assert exc_info.value.status_code == 403

    def test_missing_role_in_payload_raises_403(self):
        dependency = require_role("admin")

        with pytest.raises(HTTPException) as exc_info:
            dependency(current_user={
                "sub": "user-id",
                "email": "user@example.com",
                "role": None,
            })

        assert exc_info.value.status_code == 403
