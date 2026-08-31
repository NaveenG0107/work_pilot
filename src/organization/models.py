from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import relationship

from uuid6 import uuid7

from src.database import Base


class InvitationStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    name = Column(String(50), nullable=False, unique=True, index=True)
    created_by = Column(String(36), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    slug = Column(String(50), nullable=False, unique=True, index=True)
    domain = Column(String(150), nullable=False)
    industry = Column(String(150), nullable=False)
    team_size = Column(String, nullable=False)
    country = Column(String, nullable=False)
    logo_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    invitations = relationship("OrganizationInvitation", back_populates="organization")
    users = relationship("User", back_populates="organization")


class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(String(36), ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String(100), nullable=False, index=True)
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=True)
    token = Column(String(255), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default=InvitationStatus.PENDING)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    organization = relationship("Organization", back_populates="invitations", foreign_keys=[organization_id])
    role = relationship("Role", foreign_keys=[role_id])


class Role(Base):
    __tablename__ = "roles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(String(36), ForeignKey("organizations.id"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    organization = relationship("Organization", foreign_keys=[organization_id])
    permissions = relationship("Permission", secondary="role_permissions", back_populates="roles")


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    resource = Column(String(50), nullable=False)
    action = Column(String(50), nullable=False)

    roles = relationship("Role", secondary="role_permissions", back_populates="permissions")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id = Column(String(36), ForeignKey("roles.id"), primary_key=True)
    permission_id = Column(String(36), ForeignKey("permissions.id"), primary_key=True)


class OrphanedFile(Base):
    __tablename__ = "orphaned_files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    storage_path = Column(Text, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    available_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)