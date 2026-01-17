"""SQLite to Postgres migration utility."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Iterable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from bot.db import Base


def _resolve_url(
    explicit: str | None,
    *,
    fallback_env: str,
    allow_prefixes: Iterable[str],
) -> str | None:
    """Resolve a database URL from CLI or environment variables."""
    if explicit:
        return explicit
    candidate = os.getenv(fallback_env, "").strip()
    if candidate and any(candidate.startswith(p) for p in allow_prefixes):
        return candidate
    return None


async def _create_schema(dest_url: str, *, drop_existing: bool) -> None:
    """Create tables in the destination database."""
    dest_engine = create_async_engine(dest_url)
    async with dest_engine.begin() as conn:
        if drop_existing:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await dest_engine.dispose()


async def _truncate_tables(dest_conn: AsyncConnection) -> None:
    """Truncate destination tables to allow a clean copy."""
    for table in reversed(Base.metadata.sorted_tables):
        await dest_conn.execute(
            text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE')
        )


async def _copy_table(
    src_conn: AsyncConnection,
    dest_conn: AsyncConnection,
    table,
    src_columns: set[str],
) -> None:
    """Copy all rows for a single table."""
    columns = [table.c[name] for name in table.c.keys() if name in src_columns]
    if not columns:
        return
    try:
        result = await src_conn.stream(select(*columns))
        async for partition in result.mappings().partitions(1000):
            rows = list(partition)
            if rows:
                await dest_conn.execute(table.insert(), rows)
        return
    except Exception:
        pass

    result = await src_conn.execute(select(*columns))
    rows = result.mappings().all()
    if rows:
        await dest_conn.execute(table.insert(), rows)


async def _copy_users_table(
    src_conn: AsyncConnection,
    dest_conn: AsyncConnection,
    table,
    src_columns: set[str],
) -> None:
    """Copy users table while deferring self-referential referrer_id updates."""
    columns = [table.c[name] for name in table.c.keys() if name in src_columns]
    if not columns:
        return

    referrers = []
    if "referrer_id" in src_columns:
        result = await src_conn.execute(
            select(table.c.id, table.c.referrer_id).where(
                table.c.referrer_id.is_not(None)
            )
        )
        referrers = [{"id": row.id, "referrer_id": row.referrer_id} for row in result]

    result = await src_conn.execute(select(*columns))
    rows = []
    for row in result.mappings().all():
        payload = dict(row)
        if "referrer_id" in payload:
            payload["referrer_id"] = None
        rows.append(payload)
    if rows:
        await dest_conn.execute(table.insert(), rows)

    if referrers:
        await dest_conn.execute(
            text("UPDATE users SET referrer_id = :referrer_id WHERE id = :id"),
            referrers,
        )


async def _reset_postgres_sequences(dest_conn: AsyncConnection) -> None:
    """Reset Postgres sequences for tables with integer ID columns."""
    for table in Base.metadata.sorted_tables:
        if "id" not in table.c:
            continue
        seq = await dest_conn.scalar(
            text("SELECT pg_get_serial_sequence(:tbl, 'id')"),
            {"tbl": table.name},
        )
        if not seq:
            continue
        await dest_conn.execute(
            text(
                f"SELECT setval('{seq}', "
                f'(SELECT COALESCE(MAX(id), 1) FROM "{table.name}"), true)'
            )
        )


async def _get_sqlite_tables(src_conn: AsyncConnection) -> set[str]:
    """Fetch table names from the SQLite source."""
    result = await src_conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    )
    return {row[0] for row in result.fetchall()}


async def _get_sqlite_columns(
    src_conn: AsyncConnection, table_name: str
) -> set[str]:
    """Fetch column names for a SQLite table."""
    result = await src_conn.execute(text(f'PRAGMA table_info("{table_name}")'))
    return {row[1] for row in result.fetchall()}


async def migrate(sqlite_url: str, postgres_url: str, *, truncate: bool) -> None:
    """Copy data from SQLite to Postgres using ORM metadata."""
    await _create_schema(postgres_url, drop_existing=truncate)
    src_engine = create_async_engine(sqlite_url)
    dest_engine = create_async_engine(postgres_url)

    async with src_engine.connect() as src_conn:
        src_tables = await _get_sqlite_tables(src_conn)
        src_columns_map = {
            table_name: await _get_sqlite_columns(src_conn, table_name)
            for table_name in src_tables
        }
        if truncate:
            async with dest_engine.begin() as dest_conn:
                await _truncate_tables(dest_conn)

        for table in Base.metadata.sorted_tables:
            if table.name not in src_tables:
                print(f"Skipping missing table: {table.name}")
                continue
            print(f"Copying {table.name}...")
            try:
                async with dest_engine.begin() as dest_conn:
                    src_columns = src_columns_map.get(table.name, set())
                    if table.name == "users":
                        await _copy_users_table(
                            src_conn, dest_conn, table, src_columns
                        )
                    else:
                        await _copy_table(src_conn, dest_conn, table, src_columns)
            except Exception as exc:
                raise RuntimeError(f"Failed to copy table: {table.name}") from exc

        async with dest_engine.begin() as dest_conn:
            await _reset_postgres_sequences(dest_conn)

    await src_engine.dispose()
    await dest_engine.dispose()


def main() -> None:
    """Run the migration."""
    parser = argparse.ArgumentParser(description="Copy data from SQLite to Postgres.")
    parser.add_argument("--sqlite-url", default=None)
    parser.add_argument("--postgres-url", default=None)
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate destination tables before import.",
    )
    args = parser.parse_args()

    sqlite_url = _resolve_url(
        args.sqlite_url,
        fallback_env="SQLITE_URL",
        allow_prefixes=("sqlite",),
    )
    postgres_url = _resolve_url(
        args.postgres_url,
        fallback_env="POSTGRES_URL",
        allow_prefixes=("postgres",),
    )

    env_db_url = os.getenv("DATABASE_URL", "").strip()
    if not sqlite_url and env_db_url.startswith("sqlite"):
        sqlite_url = env_db_url
    if not postgres_url and env_db_url.startswith("postgres"):
        postgres_url = env_db_url

    if not sqlite_url or not postgres_url:
        raise SystemExit(
            "Missing database URLs. Provide --sqlite-url/--postgres-url or "
            "set SQLITE_URL/POSTGRES_URL (or DATABASE_URL for one side)."
        )

    asyncio.run(migrate(sqlite_url, postgres_url, truncate=args.truncate))


if __name__ == "__main__":
    main()
