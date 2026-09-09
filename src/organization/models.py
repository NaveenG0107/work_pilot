from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, DateTime, Integer, Index, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, foreign

from uuid6 import uuid7

from src.database import Base


class InvitationStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


from sqlalchemy.ext.hybrid import hybrid_property

class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        Index("idx_organization_deleted_at", "deleted_at"),
        Index("idx_organization_name", "name"),
        Index("idx_organization_slug", "slug", unique=True),
        UniqueConstraint("name", name="uni_organizations_name"),
        UniqueConstraint("slug", name="uni_organizations_slug"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    name = Column(String(50), nullable=False)
    created_by = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=True, default=True, server_default="true")
    slug = Column(String(50), nullable=False)
    domain = Column(String(150), nullable=False)
    industry = Column(String(150), nullable=False)
    team_size = Column(Text, nullable=False)
    country = Column(Text, nullable=False)
    logo_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    @property
    def timezone(self):
        return "UTC"

    @property
    def status(self):
        return "active" if self.is_active else "inactive"

    invitations = relationship("OrganizationInvitation", back_populates="organization")
    users = relationship("User", back_populates="organization")


class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"
    __table_args__ = (
        Index("idx_org_invites_deleted_at", "deleted_at"),
        Index("idx_org_invites_email", "email"),
        Index("idx_org_invites_org_id", "organization_id"),
        Index("idx_org_invites_token", "token"),
        UniqueConstraint("token", name="uni_organization_invitations_token"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(UUID(as_uuid=False), ForeignKey("organizations.id", name="fk_organization_invitations_organization"), nullable=False)
    email = Column(String(100), nullable=False)
    role_id = Column(UUID(as_uuid=False), ForeignKey("roles.id", name="fk_organization_invitations_role"), nullable=True)
    token = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default=InvitationStatus.PENDING, server_default="pending")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", back_populates="invitations", foreign_keys=[organization_id])
    role = relationship("Role", foreign_keys=[role_id])


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        Index("idx_roles_deleted_at", "deleted_at"),
        Index("idx_roles_organization_id", "organization_id"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", primaryjoin="foreign(Role.organization_id) == Organization.id", foreign_keys=[organization_id])
    permissions = relationship("Permission", secondary="role_permissions", back_populates="roles")


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    resource = Column(String(50), nullable=False)
    action = Column(String(50), nullable=False)

    roles = relationship("Role", secondary="role_permissions", back_populates="permissions")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id = Column(UUID(as_uuid=False), ForeignKey("roles.id", name="fk_role_permissions_role"), primary_key=True)
    permission_id = Column(UUID(as_uuid=False), ForeignKey("permissions.id", name="fk_role_permissions_permission"), primary_key=True)


class OrphanedFile(Base):
    __tablename__ = "orphaned_files"
    __table_args__ = (Index("idx_orphaned_files_available", "available_at", "created_at"),)

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    storage_path = Column(Text, nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    available_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
