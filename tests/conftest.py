import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.jwt_auth.models import User, RefreshToken
from src.utils.jwt_handler import create_access_token, create_refresh_token


SECRET = "test-secret-key"
SAMPLE_USER_ID = str(uuid.uuid4())
SAMPLE_EMAIL = "test@example.com"
SAMPLE_ROLE = "user"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def access_token():
    return create_access_token(
        user_id=SAMPLE_USER_ID,
        email=SAMPLE_EMAIL,
        role=SAMPLE_ROLE,
    )


@pytest.fixture
def admin_access_token():
    return create_access_token(
        user_id=SAMPLE_USER_ID,
        email="admin@example.com",
        role="admin",
    )


@pytest.fixture
def refresh_token():
    return create_refresh_token(
        user_id=SAMPLE_USER_ID,
        email=SAMPLE_EMAIL,
        role=SAMPLE_ROLE,
    )


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.user_id = uuid.UUID(SAMPLE_USER_ID)
    user.email_address = SAMPLE_EMAIL
    user.is_active = True
    user.password_hash = "hashed_password"
    user.role = "user"
    return user


@pytest.fixture
def mock_inactive_user():
    user = MagicMock(spec=User)
    user.user_id = uuid.UUID(SAMPLE_USER_ID)
    user.email_address = SAMPLE_EMAIL
    user.is_active = False
    user.password_hash = "hashed_password"
    user.role = "user"
    return user


@pytest.fixture
def mock_refresh_token_record():
    record = MagicMock(spec=RefreshToken)
    record.jti = str(uuid.uuid4())
    record.access_token_jti = str(uuid.uuid4())
    record.is_revoked = False
    return record
