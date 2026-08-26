import secrets

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, DateTime, event
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(String(36), ForeignKey("organizations.id"), nullable=True, index=True)
    full_name = Column(String(100), nullable=False)
    username = Column(String(30), nullable=False, unique=True, index=True)
    email = Column(String(100), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=False, index=True)
    avatar_url = Column(String(500), nullable=True)
    color = Column(String(7), nullable=False, default="#3498DB")
    timezone = Column(String(50), nullable=True, default="UTC")
    is_active = Column(Boolean, nullable=False, default=False)
    is_verified = Column(Boolean, nullable=False, default=False)
    status = Column(String(50), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    joined_at = Column(DateTime(timezone=True), nullable=True)
    require_password_change = Column(Boolean, nullable=False, default=False)

    organization = relationship("Organization", foreign_keys=[organization_id])
    role = relationship("Role", foreign_keys=[role_id])


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    token_hash = Column(String(255), nullable=False, unique=True)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    user = relationship("User", foreign_keys=[user_id])


def generate_random_hex_color():
    try:
        return "#" + secrets.token_hex(3).upper()
    except Exception:
        return "#3498DB"


@event.listens_for(User, "before_insert")
def user_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())

    if not target.color:
        target.color = generate_random_hex_color()


@event.listens_for(RefreshToken, "before_insert")
def refresh_token_before_insert(mapper, connection, target):
    if not target.id:
        target.id = str(uuid7())