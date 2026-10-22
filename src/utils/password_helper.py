"""
Password hashing, verification, and policy validation utilities for WorkPilot.
Supports Argon2 (via pwdlib) and standard Bcrypt (matching Go DefaultCost).
"""

import re
from typing import Optional, Tuple

import bcrypt
from pwdlib import PasswordHash

# Default password hashing algorithm (Argon2id)
password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash password using pwdlib (Argon2id)."""
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify password using pwdlib with automatic fallback to bcrypt."""
    try:
        return password_hash.verify(password, hashed_password)
    except Exception:
        return bcrypt_verify(password, hashed_password)


def bcrypt_hash(password: str) -> Tuple[str, None]:
    """Hash password with bcrypt cost 10 (Go DefaultCost). Returns (hashed_pw, None)."""
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
    return hashed, None


def bcrypt_verify(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash. Returns True/False."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def validate_password(password: str) -> bool:
    """Returns True if password meets policy: >=8 chars, upper+lower+digit+special, no spaces."""
    if len(password) < 8:
        return False
    if "\\s" in password or re.search(r"\s", password):
        return False
    has_upper = re.search(r"[A-Z]", password) is not None
    has_lower = re.search(r"[a-z]", password) is not None
    has_digit = re.search(r"[0-9]", password) is not None
    has_special = (
        re.search(r'[!@#$%^&*()_+\-=\[\]{}|\\:;"\'<>,.?/~]', password)
        is not None
    )
    return has_upper and has_lower and has_digit and has_special