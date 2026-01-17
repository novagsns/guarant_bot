"""Database migration utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class Migration:
    """Represent a database migration step.

    Attributes:
        version: Migration version identifier.
        description: Human-readable description for operators.
        apply: Async function that applies the migration.
        destructive: Whether the migration performs destructive changes.
    """

    version: str
    description: str
    apply: Callable[[AsyncConnection, str], Awaitable[None]]
    destructive: bool = False


async def _ensure_migrations_table(conn: AsyncConnection, dialect_name: str) -> None:
    """Create the migrations table if it is missing.

    Args:
        conn: Active database connection.
        dialect_name: SQLAlchemy dialect name.
    """
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(64) PRIMARY KEY, "
            "applied_at TIMESTAMPTZ DEFAULT NOW())"
        )
    )


async def _get_applied_migrations(
    conn: AsyncConnection,
    dialect_name: str,
) -> set[str]:
    """Fetch already applied migration versions.

    Args:
        conn: Active database connection.
        dialect_name: SQLAlchemy dialect name.

    Returns:
        Set of applied migration versions.
    """
    await _ensure_migrations_table(conn, dialect_name)
    result = await conn.execute(text("SELECT version FROM schema_migrations"))
    return {row[0] for row in result.fetchall()}


async def get_pending_migrations(
    conn: AsyncConnection,
    dialect_name: str,
    migrations: Iterable[Migration],
) -> list[Migration]:
    """Determine which migrations still need to be applied.

    Args:
        conn: Active database connection.
        dialect_name: SQLAlchemy dialect name.
        migrations: Iterable of migration steps.

    Returns:
        Ordered list of pending migrations.
    """
    applied = await _get_applied_migrations(conn, dialect_name)
    return [migration for migration in migrations if migration.version not in applied]


async def run_migrations(
    conn: AsyncConnection,
    dialect_name: str,
    migrations: Iterable[Migration],
    *,
    allow_destructive: bool,
) -> list[Migration]:
    """Apply all pending migrations in order.

    Args:
        conn: Active database connection.
        dialect_name: SQLAlchemy dialect name.
        migrations: Iterable of migration steps.

    Returns:
        List of migrations that were applied.
    """
    pending = await get_pending_migrations(conn, dialect_name, migrations)
    destructive = [migration for migration in pending if migration.destructive]
    if destructive and not allow_destructive:
        versions = ", ".join(migration.version for migration in destructive)
        raise RuntimeError(
            "Destructive migrations are blocked. "
            "Set DB_ALLOW_DESTRUCTIVE_MIGRATIONS=1 to proceed. "
            f"Pending destructive migrations: {versions}"
        )
    for migration in pending:
        await migration.apply(conn, dialect_name)
        await conn.execute(
            text("INSERT INTO schema_migrations (version) VALUES (:version)"),
            {"version": migration.version},
        )
    return pending
