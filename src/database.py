import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not configured")

# Normalize the URL to the async-capable psycopg driver if a sync driver
# (psycopg2) was configured. psycopg v3 supports both sync and async.
if DATABASE_URL.startswith("postgresql+psycopg2://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql+psycopg2://", "postgresql+psycopg://", 1
    )
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )


engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Redis client (lazy singleton)
# ---------------------------------------------------------------------------

_REDIS_CLIENT = None


def _build_redis_url() -> str:
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD", "")
    if password:
        return f"redis://:{password}@{host}:{port}/0"
    return f"redis://{host}:{port}/0"


def get_redis():
    """Return a shared async Redis client (redis.asyncio)."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        import redis.asyncio as aioredis

        _REDIS_CLIENT = aioredis.from_url(
            _build_redis_url(),
            encoding="utf-8",
            decode_responses=True,
        )
    return _REDIS_CLIENT
