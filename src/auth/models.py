import secrets

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, DateTime, Index, UniqueConstraint, text, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, foreign

from uuid6 import uuid7

from src.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uni_users_username"),
        UniqueConstraint("email", name="uni_users_email"),
        Index("idx_users_organization_id", "organization_id"),
        Index("idx_users_username", "username"),
        Index("idx_users_email", "email"),
        Index("idx_users_deleted_at", "deleted_at"),
        Index("idx_users_role_id", "role_id"),
        Index("idx_users_fts", text("to_tsvector('english', coalesce(full_name, '') || ' ' || coalesce(email, '') || ' ' || coalesce(username, ''))"), postgresql_using="gin"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(UUID(as_uuid=False), ForeignKey("organizations.id", name="fk_users_organization"), nullable=True)
    full_name = Column(String(100), nullable=False)
    username = Column(String(30), nullable=False)
    email = Column(String(100), nullable=False)
    password_hash = Column(Text, nullable=True)
    role_id = Column(UUID(as_uuid=False), ForeignKey("roles.id", name="fk_users_role"), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    color = Column(String(7), nullable=False, default="#3498DB", server_default="#3498DB")
    timezone = Column(String(50), nullable=True, default="UTC", server_default="UTC")
    is_active = Column(Boolean, nullable=True, default=True)
    is_verified = Column(Boolean, nullable=True, default=False)
    status = Column(String(50), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    joined_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    require_password_change = Column(Boolean, nullable=False, default=False, server_default="false")

    organization = relationship("Organization", foreign_keys=[organization_id])
    role = relationship("Role", foreign_keys=[role_id])


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uni_refresh_tokens_token_hash"),
        UniqueConstraint("user_id", name="uni_refresh_tokens_user_id"),
        Index("idx_refresh_tokens_user_id", "user_id"),
        Index("idx_refresh_tokens_deleted_at", "deleted_at"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(UUID(as_uuid=False), nullable=False)
    token_hash = Column(String(255), nullable=False)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", primaryjoin="foreign(RefreshToken.user_id) == User.id", foreign_keys=[user_id])


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
