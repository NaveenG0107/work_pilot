from datetime import datetime, timedelta, timezone
import uuid
import jwt

from src.config import (
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_REFRESH_TOKEN_EXPIRE_DAYS,
    JWT_SECRET_KEY,
)


def create_access_token(user_id: str, email: str, role: str) -> str:

    now = datetime.now(timezone.utc)

    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes = JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm = JWT_ALGORITHM)

def create_refresh_token(user_id: str, email: str, role: str) -> str:

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days = JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm = JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET_KEY, algorithms = [JWT_ALGORITHM])