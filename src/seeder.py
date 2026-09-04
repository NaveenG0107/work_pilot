# src/seeder.py
"""
Database seeder for WorkPilot FastAPI application.
Seeds initial data including permissions and countries from SQL/models.

Usage:
    python -m src.seeder                  # Seeds all (permissions, countries)
    python -m src.seeder --permissions    # Seeds only permissions
    python -m src.seeder --countries      # Seeds only countries
"""

import argparse
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid6 import uuid7

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import AsyncSessionLocal, engine

# Ensure all SQLAlchemy models are registered in Base.metadata
import src.audit.models
import src.auth.models
import src.comments.models
import src.custom_status.models
import src.favorite.models
import src.label.models
import src.organization.models
import src.project.models
import src.public.models
import src.serial.models
import src.sprint.models
import src.task.models
import src.user_story.models
import src.user_story_status.models
from src.auth.models import User
from src.organization.models import Permission, Role
from src.public.models import Country
from src.utils.password_helper import hash_password

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("seeder")

# ---------------------------------------------------------------------------
# 1. Permissions Seed Data
# ---------------------------------------------------------------------------
DEFAULT_PERMISSIONS: List[Tuple[str, str]] = [
    # Projects
    ("projects", "view"),
    ("projects", "add"),
    ("projects", "modify"),
    ("projects", "delete"),
    # Sprints
    ("sprints", "view"),
    ("sprints", "add"),
    ("sprints", "modify"),
    ("sprints", "delete"),
    # User Stories
    ("user_stories", "view"),
    ("user_stories", "add"),
    ("user_stories", "modify"),
    ("user_stories", "delete"),
    # Tasks
    ("tasks", "view"),
    ("tasks", "add"),
    ("tasks", "modify"),
    ("tasks", "delete"),
    # Comments
    ("comments", "view"),
    ("comments", "add"),
    ("comments", "modify"),
    ("comments", "delete"),
    ("comments", "comment"),
]


async def seed_permissions(session: AsyncSession) -> int:
    """
    Idempotently seeds all default system permissions using SQLAlchemy ORM.
    """
    logger.info("Seeding permissions...")
    seeded_count = 0

    # 1. Query existing permissions
    result = await session.execute(select(Permission.resource, Permission.action))
    existing_perms = {(row[0], row[1]) for row in result.all()}

    # 2. Insert only missing permissions
    for resource, action in DEFAULT_PERMISSIONS:
        if (resource, action) not in existing_perms:
            session.add(
                Permission(
                    id=str(uuid7()),
                    resource=resource,
                    action=action,
                )
            )
            seeded_count += 1
            existing_perms.add((resource, action))

    await session.commit()
    logger.info(
        f"Permissions seeded: {seeded_count} newly added (total defined: {len(DEFAULT_PERMISSIONS)})."
    )
    return seeded_count


# ---------------------------------------------------------------------------
# 2. Countries Seed Data
# ---------------------------------------------------------------------------
def _find_countries_sql_file(custom_path: Optional[str] = None) -> Optional[Path]:
    """Finds the countries seed SQL file from several candidate locations."""
    if custom_path and Path(custom_path).is_file():
        return Path(custom_path)

    base_dir = Path(__file__).resolve().parent
    workspace_dir = base_dir.parent.parent

    candidates = [
        base_dir / "utils" / "seeds" / "seed_countries.sql",
        base_dir.parent / "src" / "utils" / "seeds" / "seed_countries.sql",
        Path("/app/src/utils/seeds/seed_countries.sql"),
        base_dir.parent / "seed_countries.sql",
        base_dir.parent / "seed_countries 1.sql",
        workspace_dir / "seed_countries 1.sql",
        workspace_dir / "seed_countries.sql",
        Path("seed_countries 1.sql"),
        Path("seed_countries.sql"),
        Path("/app/seed_countries 1.sql"),
        Path("/app/seed_countries.sql"),
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    return None


async def seed_countries(session: AsyncSession, sql_file_path: Optional[str] = None) -> int:
    """
    Idempotently seeds ISO countries by directly executing the PostgreSQL seed SQL script.
    """
    logger.info("Seeding countries...")
    filepath = _find_countries_sql_file(sql_file_path)

    if not filepath or not filepath.is_file():
        logger.error(
            "Countries SQL seed file not found! Checked locations include 'src/utils/seeds/seed_countries.sql'."
        )
        return 0

    logger.info(f"Reading countries seed file: {filepath}")
    sql_content = filepath.read_text(encoding="utf-8")

    conn = await session.connection()
    await conn.exec_driver_sql(sql_content)
    await session.commit()

    # Query total countries count to verify database state
    result = await session.execute(select(Country.id))
    total_in_db = len(result.all())

    logger.info(
        f"Countries seeded successfully from SQL script (Total in DB: {total_in_db})."
    )
    return total_in_db


# ---------------------------------------------------------------------------
# 3. Super Admin Seed Data
# ---------------------------------------------------------------------------
async def seed_super_admin(
    session: AsyncSession,
    email: str = "superadmin@gmail.com",
    username: str = "superadmin",
    password: str = "Password@123",
    full_name: str = "Super Admin",
) -> None:
    """
    Idempotently seeds the super_admin system role and default superadmin user.
    """
    logger.info("Seeding super_admin role and user...")
    now = datetime.now(timezone.utc)

    # 1. Seed or get super_admin role
    result = await session.execute(
        select(Role).where(
            Role.name == "super_admin",
            Role.organization_id.is_(None),
            Role.deleted_at.is_(None),
        )
    )
    role = result.scalar_one_or_none()

    if not role:
        role = Role(
            id=str(uuid7()),
            organization_id=None,
            name="super_admin",
            description="System administrator with global access",
            is_system=True,
            created_at=now,
            updated_at=now,
        )
        session.add(role)
        await session.flush()
        logger.info("Created 'super_admin' system role.")
    else:
        logger.info("Role 'super_admin' already exists.")

    # 2. Seed or get super_admin user
    result = await session.execute(
        select(User).where(
            (User.email == email) | (User.username == username),
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            id=str(uuid7()),
            organization_id=None,
            full_name=full_name,
            username=username,
            email=email,
            password_hash=hash_password(password),
            role_id=role.id,
            is_active=True,
            is_verified=True,
            color="#E74C3C",
            status="active",
            require_password_change=False,
            created_at=now,
            joined_at=now,
            updated_at=now,
        )
        session.add(user)
        logger.info(f"Created default super_admin user '{username}' ({email}).")
    else:
        # Ensure role_id points to super_admin role and user is active/verified
        user.role_id = role.id
        user.is_active = True
        user.is_verified = True
        user.updated_at = now
        logger.info(f"User '{username}' already exists; verified super_admin role assignment.")

    await session.commit()


# ---------------------------------------------------------------------------
# 4. Main Runner
# ---------------------------------------------------------------------------
async def run_seeders(
    seed_perms: bool = True,
    seed_cntrs: bool = True,
    seed_admin: bool = True,
    countries_sql_path: Optional[str] = None,
) -> None:
    """Runs the requested seeders."""
    async with AsyncSessionLocal() as session:
        try:
            if seed_perms:
                await seed_permissions(session)
            if seed_cntrs:
                await seed_countries(session, countries_sql_path)
            if seed_admin:
                await seed_super_admin(session)
            logger.info("All selected seeding tasks completed successfully.")
        except Exception as exc:
            await session.rollback()
            logger.error(f"Seeding failed: {exc}", exc_info=True)
            raise
        finally:
            await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="WorkPilot Database Seeder")
    parser.add_argument(
        "--permissions",
        action="store_true",
        help="Seed only default system permissions",
    )
    parser.add_argument(
        "--countries",
        action="store_true",
        help="Seed only ISO countries",
    )
    parser.add_argument(
        "--super-admin",
        action="store_true",
        help="Seed only super_admin role and user",
    )
    parser.add_argument(
        "--countries-file",
        type=str,
        default=None,
        help="Custom path to seed_countries.sql file",
    )

    args = parser.parse_args()

    # If neither flag is specifically passed, seed all by default
    seed_all = not (args.permissions or args.countries or args.super_admin)
    seed_perms = seed_all or args.permissions
    seed_cntrs = seed_all or args.countries
    seed_admin = seed_all or args.super_admin

    asyncio.run(
        run_seeders(
            seed_perms=seed_perms,
            seed_cntrs=seed_cntrs,
            seed_admin=seed_admin,
            countries_sql_path=args.countries_file,
        )
    )


if __name__ == "__main__":
    main()
