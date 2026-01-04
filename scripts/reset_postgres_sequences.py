"""Reset Postgres sequences to the current max(id)."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bot.db import Base


async def reset_sequences() -> None:
    """Reset sequences for tables with integer primary keys."""
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith("postgres"):
        raise SystemExit("DATABASE_URL must point to Postgres.")

    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if "id" not in table.c:
                continue
            seq = await conn.scalar(
                text("SELECT pg_get_serial_sequence(:tbl, 'id')"),
                {"tbl": table.name},
            )
            if not seq:
                continue
            await conn.execute(
                text(
                    f"SELECT setval('{seq}', "
                    f'(SELECT COALESCE(MAX(id), 1) FROM "{table.name}"), true)'
                )
            )
    await engine.dispose()


def main() -> None:
    """Run sequence reset."""
    asyncio.run(reset_sequences())


if __name__ == "__main__":
    main()
