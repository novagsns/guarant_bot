"""Module for session functionality."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """Create engine.

    Args:
        database_url: Value for database_url.

    Returns:
        Return value.
    """
    return create_async_engine(database_url, echo=False, future=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create sessionmaker.

    Args:
        engine: Value for engine.

    Returns:
        Return value.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
