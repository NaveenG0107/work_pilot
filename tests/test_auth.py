from unittest.mock import patch, MagicMock

import pytest


class TestLoginEndpoint:
    @patch("src.jwt_auth.api.create_tokens")
    @patch("src.jwt_auth.api.authenticate_user")
    def test_login_success(self, mock_auth, mock_tokens, client, mock_user):
        mock_auth.return_value = mock_user
        mock_tokens.return_value = ("access.token", "refresh.token")

        response = client.post(
            "/auth/login",
            json={
                "email_address": "test@example.com",
                "password": "Test@123",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status_code"] == 200
        assert data["message"] == "login successfully"
        assert data["data"]["access_token"] == "access.token"
        assert data["data"]["refresh_token"] == "refresh.token"

    @patch("src.jwt_auth.api.authenticate_user")
    def test_login_invalid_credentials(self, mock_auth, client):
        mock_auth.return_value = None

        response = client.post(
            "/auth/login",
            json={
                "email_address": "wrong@example.com",
                "password": "wrongpass",
            },
        )

        assert response.status_code == 401
        assert "Invalid email address or password" in response.json()["detail"]

    @patch("src.jwt_auth.api.authenticate_user")
    def test_login_inactive_user(self, mock_auth, client, mock_inactive_user):
        mock_auth.return_value = None

        response = client.post(
            "/auth/login",
            json={
                "email_address": "inactive@example.com",
                "password": "Test@123",
            },
        )

        assert response.status_code == 401

    @patch("src.jwt_auth.api.authenticate_user")
    def test_login_service_exception(self, mock_auth, client):
        mock_auth.side_effect = Exception("DB error")

        response = client.post(
            "/auth/login",
            json={
                "email_address": "test@example.com",
                "password": "Test@123",
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"


class TestRefreshTokenEndpoint:
    @patch("src.jwt_auth.api.refresh_access_token")
    def test_refresh_success(self, mock_refresh, client, refresh_token):
        mock_refresh.return_value = "new.access.token"

        response = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status_code"] == 200
        assert data["data"]["access_token"] == "new.access.token"

    @patch("src.jwt_auth.api.refresh_access_token")
    def test_refresh_invalid_token(self, mock_refresh, client):
        mock_refresh.return_value = None

        response = client.post(
            "/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )

        assert response.status_code == 401
        assert "Invalid or expired refresh token" in response.json()["detail"]

    @patch("src.jwt_auth.api.refresh_access_token")
    def test_refresh_service_exception(self, mock_refresh, client, refresh_token):
        mock_refresh.side_effect = Exception("DB error")

        response = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 500


class TestLogoutEndpoint:
    @patch("src.jwt_auth.api.revoke_refresh_token")
    def test_logout_success(self, mock_revoke, client, refresh_token):
        mock_revoke.return_value = True

        response = client.post(
            "/auth/logout",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Logout successfully"
        assert data["data"] is None

    @patch("src.jwt_auth.api.revoke_refresh_token")
    def test_logout_already_revoked(self, mock_revoke, client, refresh_token):
        mock_revoke.return_value = False

        response = client.post(
            "/auth/logout",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 401
        assert "Invalid or already revoked" in response.json()["detail"]

    @patch("src.jwt_auth.api.revoke_refresh_token")
    def test_logout_invalid_token(self, mock_revoke, client):
        mock_revoke.return_value = False

        response = client.post(
            "/auth/logout",
            json={"refresh_token": "invalid.token"},
        )

        assert response.status_code == 401

    @patch("src.jwt_auth.api.revoke_refresh_token")
    def test_logout_service_exception(self, mock_revoke, client, refresh_token):
        mock_revoke.side_effect = Exception("DB error")

        response = client.post(
            "/auth/logout",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 500


class TestProfileEndpoint:
    def test_profile_success(self, client):
        from src.main import app
        from src.services.jwt_auth.dependancy import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-id-123",
            "email": "test@example.com",
            "role": "user",
        }

        response = client.get(
            "/auth/profile",
            headers={"Authorization": "Bearer fake.access.token"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["user_id"] == "user-id-123"
        assert data["data"]["email"] == "test@example.com"
        assert data["data"]["role"] == "user"

    def test_profile_no_token(self, client):
        response = client.get("/auth/profile")
        assert response.status_code == 401

    def test_profile_invalid_token(self, client):
        response = client.get(
            "/auth/profile",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401


class TestAdminProfileEndpoint:
    def test_admin_profile_success(self, client):
        from src.main import app
        from src.services.jwt_auth.dependancy import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-id-123",
            "email": "admin@example.com",
            "role": "admin",
        }

        response = client.get(
            "/auth/admin/profile",
            headers={"Authorization": "Bearer fake.admin.token"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["data"]["role"] == "admin"

    def test_admin_profile_no_token(self, client):
        response = client.get("/auth/admin/profile")
        assert response.status_code == 401


class TestUserProfileEndpoint:
    def test_user_profile_success(self, client):
        from src.main import app
        from src.services.jwt_auth.dependancy import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-id-123",
            "email": "test@example.com",
            "role": "user",
        }

        response = client.get(
            "/auth/user/profile",
            headers={"Authorization": "Bearer fake.user.token"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["data"]["role"] == "user"

    def test_user_profile_no_token(self, client):
        response = client.get("/auth/user/profile")
        assert response.status_code == 401


class TestHealthCheck:
    def test_health_check(self, client):
        response = client.get("/health_check")
        assert response.status_code == 200
        assert "running" in response.json()["message"]
