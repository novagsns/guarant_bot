"""Database schema preparation and migrations."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from bot.db import Base
from bot.db.migrations import Migration, get_pending_migrations, run_migrations


async def prepare_database(
    engine: AsyncEngine,
    *,
    allow_destructive: bool,
) -> None:
    """Prepare the database schema and apply safe migrations.

    This function creates missing tables and applies non-destructive migrations.

    Args:
        engine: SQLAlchemy async engine.
        allow_destructive: Whether destructive migrations are allowed.
    """
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Only Postgres is supported.")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        pending = await get_pending_migrations(
            conn,
            engine.dialect.name,
            _get_migrations(),
        )
        await run_migrations(
            conn,
            engine.dialect.name,
            pending,
            allow_destructive=allow_destructive,
        )


async def apply_schema_updates(conn: AsyncConnection, dialect_name: str) -> None:
    """Apply safe schema updates (add-only, no data drops).

    Args:
        conn: Active database connection.
        dialect_name: SQLAlchemy dialect name.
    """
    await _ensure_ads_media_columns(conn, dialect_name)
    await _ensure_user_profile_columns(conn, dialect_name)
    await _ensure_user_vip_columns(conn, dialect_name)
    await _ensure_user_trust_columns(conn, dialect_name)
    await _ensure_ads_moderation_columns(conn, dialect_name)
    await _ensure_topup_columns(conn, dialect_name)
    await _ensure_wallet_tx_columns(conn, dialect_name)
    await _ensure_service_media_columns(conn, dialect_name)
    await _ensure_support_ticket_columns(conn, dialect_name)
    await _ensure_ad_account_columns(conn, dialect_name)
    await _ensure_ads_promoted_columns(conn, dialect_name)
    await _ensure_ads_kind_columns(conn, dialect_name)
    await _ensure_deal_columns(conn, dialect_name)
    await _ensure_deal_room_table(conn, dialect_name)
    await _ensure_deal_message_table(conn, dialect_name)
    await _ensure_topic_activity_tables(conn, dialect_name)
    await _ensure_coin_drop_table(conn, dialect_name)
    await _ensure_dispute_columns(conn, dialect_name)
    await _ensure_review_unique_index(conn, dialect_name)


def _get_migrations() -> Iterable[Migration]:
    """Return the list of schema migrations in order."""
    return [
        Migration(
            version="20260104_schema_additions",
            description="Ensure missing columns and indexes are present.",
            apply=apply_schema_updates,
        ),
    ]


async def _ensure_ads_media_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure ads media columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ads'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "media_type" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN media_type VARCHAR(16)"))
    if "media_file_id" not in columns:
        await conn.execute(
            text("ALTER TABLE ads ADD COLUMN media_file_id VARCHAR(256)")
        )


async def _ensure_user_profile_columns(
    conn: AsyncConnection, dialect_name: str
) -> None:
    """Handle ensure user profile columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "balance" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN balance NUMERIC(14,2) DEFAULT 0")
        )
    if "rating_avg" not in columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN rating_avg NUMERIC(3,2)"))
    if "rating_count" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN rating_count INTEGER DEFAULT 0")
        )
    if "on_shift" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN on_shift BOOLEAN DEFAULT 0")
        )
    if "referrer_id" not in columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN referrer_id INTEGER"))


async def _ensure_user_trust_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure user trust columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "verified" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN verified BOOLEAN DEFAULT 0")
        )


async def _ensure_ads_moderation_columns(
    conn: AsyncConnection, dialect_name: str
) -> None:
    """Handle ensure ads moderation columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ads'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "moderation_status" not in columns:
        await conn.execute(
            text("ALTER TABLE ads ADD COLUMN moderation_status VARCHAR(16)")
        )
        await conn.execute(text("UPDATE ads SET moderation_status = 'approved'"))
    if "moderation_reason" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN moderation_reason TEXT"))
    if "title_html" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN title_html TEXT"))
    if "description_html" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN description_html TEXT"))
    await conn.execute(
        text(
            "UPDATE ads SET title_html = "
            "REPLACE(REPLACE(REPLACE(title, '&', '&amp;'), '<', '&lt;'), '>', '&gt;') "
            "WHERE title_html IS NULL"
        )
    )
    await conn.execute(
        text(
            "UPDATE ads SET description_html = "
            "REPLACE(REPLACE(REPLACE(description, '&', '&amp;'), '<', '&lt;'), '>', '&gt;') "
            "WHERE description_html IS NULL"
        )
    )


async def _ensure_user_vip_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure user vip columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "vip_until" not in columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN vip_until TIMESTAMP"))
    if "free_fee_until" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN free_fee_until TIMESTAMP")
        )
    if "paid_broadcasts_date" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN paid_broadcasts_date TIMESTAMP")
        )
    if "paid_broadcasts_count" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN paid_broadcasts_count INTEGER DEFAULT 0")
        )


async def _ensure_topup_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure topup columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'topups'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "amount_rub" not in columns:
        await conn.execute(
            text("ALTER TABLE topups ADD COLUMN amount_rub NUMERIC(14,2)")
        )
    if "amount_usdt" not in columns:
        await conn.execute(
            text("ALTER TABLE topups ADD COLUMN amount_usdt NUMERIC(14,6)")
        )


async def _ensure_wallet_tx_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure wallet tx columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'wallet_transactions'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "ref_type" not in columns:
        await conn.execute(
            text("ALTER TABLE wallet_transactions ADD COLUMN ref_type VARCHAR(32)")
        )
    if "ref_id" not in columns:
        await conn.execute(
            text("ALTER TABLE wallet_transactions ADD COLUMN ref_id INTEGER")
        )


async def _ensure_service_media_columns(
    conn: AsyncConnection, dialect_name: str
) -> None:
    """Handle ensure service media columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'services'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "media_type" not in columns:
        await conn.execute(
            text("ALTER TABLE services ADD COLUMN media_type VARCHAR(16)")
        )
    if "media_file_id" not in columns:
        await conn.execute(
            text("ALTER TABLE services ADD COLUMN media_file_id VARCHAR(256)")
        )


async def _ensure_support_ticket_columns(
    conn: AsyncConnection, dialect_name: str
) -> None:
    """Handle ensure support ticket columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'support_tickets'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "last_message" not in columns:
        await conn.execute(
            text("ALTER TABLE support_tickets ADD COLUMN last_message TEXT")
        )
    if "assignee_id" not in columns:
        await conn.execute(
            text("ALTER TABLE support_tickets ADD COLUMN assignee_id BIGINT")
        )


async def _ensure_ad_account_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure ad account columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ads'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "account_id" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN account_id VARCHAR(64)"))


async def _ensure_ads_promoted_columns(
    conn: AsyncConnection, dialect_name: str
) -> None:
    """Handle ensure ads promoted columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ads'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "promoted_at" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN promoted_at TIMESTAMP"))


async def _ensure_ads_kind_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure ads kind columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ads'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "ad_kind" not in columns:
        await conn.execute(
            text("ALTER TABLE ads ADD COLUMN ad_kind VARCHAR(16) DEFAULT 'sale'")
        )
        await conn.execute(text("UPDATE ads SET ad_kind = 'sale'"))


async def _ensure_deal_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure deal columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'deals'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "closed_at" not in columns:
        await conn.execute(text("ALTER TABLE deals ADD COLUMN closed_at TIMESTAMP"))
    if "room_chat_id" not in columns:
        await conn.execute(text("ALTER TABLE deals ADD COLUMN room_chat_id BIGINT"))
    if "room_invite_link" not in columns:
        await conn.execute(text("ALTER TABLE deals ADD COLUMN room_invite_link TEXT"))
    if "room_ready" not in columns:
        await conn.execute(
            text("ALTER TABLE deals ADD COLUMN room_ready BOOLEAN DEFAULT 0")
        )


async def _ensure_deal_room_table(conn: AsyncConnection, dialect_name: str) -> None:
    """Ensure deal_rooms table exists."""
    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'deal_rooms'"
        )
    )
    exists = result.first() is not None
    if not exists:
        await conn.execute(
            text(
                "CREATE TABLE deal_rooms ("
                "id SERIAL PRIMARY KEY,"
                "chat_id BIGINT UNIQUE NOT NULL,"
                "title VARCHAR(255),"
                "invite_link TEXT,"
                "active BOOLEAN DEFAULT TRUE,"
                "created_by BIGINT REFERENCES users(id),"
                "assigned_deal_id INTEGER REFERENCES deals(id),"
                "created_at TIMESTAMPTZ DEFAULT now()"
                ")"
            )
        )


async def _ensure_deal_message_table(conn: AsyncConnection, dialect_name: str) -> None:
    """Ensure deal_messages table exists."""
    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'deal_messages'"
        )
    )
    exists = result.first() is not None
    if not exists:
        await conn.execute(
            text(
                "CREATE TABLE deal_messages ("
                "id SERIAL PRIMARY KEY,"
                "deal_id INTEGER NOT NULL REFERENCES deals(id),"
                "sender_id BIGINT NOT NULL,"
                "sender_role VARCHAR(16) NOT NULL,"
                "message_type VARCHAR(16) NOT NULL,"
                "text TEXT,"
                "file_id VARCHAR(256),"
                "created_at TIMESTAMPTZ DEFAULT now()"
                ")"
            )
        )


async def _ensure_topic_activity_tables(
    conn: AsyncConnection, dialect_name: str
) -> None:
    """Ensure topic activity tables exist."""
    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'topic_activity_meta'"
        )
    )
    exists = result.first() is not None
    if not exists:
        await conn.execute(
            text(
                "CREATE TABLE topic_activity_meta ("
                "id SERIAL PRIMARY KEY,"
                "chat_id BIGINT NOT NULL,"
                "topic_id INTEGER NOT NULL,"
                "pinned_message_id INTEGER,"
                "period_start TIMESTAMPTZ,"
                "last_reward_at TIMESTAMPTZ,"
                "updated_at TIMESTAMPTZ DEFAULT now(),"
                "UNIQUE(chat_id, topic_id)"
                ")"
            )
        )

    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'topic_activity_stats'"
        )
    )
    exists = result.first() is not None
    if not exists:
        await conn.execute(
            text(
                "CREATE TABLE topic_activity_stats ("
                "id SERIAL PRIMARY KEY,"
                "chat_id BIGINT NOT NULL,"
                "topic_id INTEGER NOT NULL,"
                "user_id BIGINT NOT NULL,"
                "username VARCHAR(64),"
                "full_name VARCHAR(128),"
                "message_count INTEGER DEFAULT 0,"
                "last_counted_at TIMESTAMPTZ,"
                "created_at TIMESTAMPTZ DEFAULT now(),"
                "updated_at TIMESTAMPTZ,"
                "UNIQUE(chat_id, topic_id, user_id)"
                ")"
            )
        )

    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'topic_activity_rewards'"
        )
    )
    exists = result.first() is not None
    if not exists:
        await conn.execute(
            text(
                "CREATE TABLE topic_activity_rewards ("
                "id SERIAL PRIMARY KEY,"
                "chat_id BIGINT NOT NULL,"
                "topic_id INTEGER NOT NULL,"
                "user_id BIGINT NOT NULL,"
                "amount INTEGER NOT NULL,"
                "status VARCHAR(16) DEFAULT 'pending',"
                "period_start TIMESTAMPTZ,"
                "created_at TIMESTAMPTZ DEFAULT now(),"
                "granted_at TIMESTAMPTZ"
                ")"
            )
        )


async def _ensure_coin_drop_table(conn: AsyncConnection, dialect_name: str) -> None:
    """Ensure coin_drops table exists."""
    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'coin_drops'"
        )
    )
    exists = result.first() is not None
    if not exists:
        await conn.execute(
            text(
                "CREATE TABLE coin_drops ("
                "id SERIAL PRIMARY KEY,"
                "chat_id BIGINT NOT NULL,"
                "topic_id INTEGER,"
                "message_id INTEGER,"
                "created_by BIGINT NOT NULL REFERENCES users(id),"
                "claimed_by BIGINT REFERENCES users(id),"
                "claimed_username VARCHAR(64),"
                "amount INTEGER,"
                "credited BOOLEAN DEFAULT FALSE,"
                "created_at TIMESTAMPTZ DEFAULT now(),"
                "claimed_at TIMESTAMPTZ,"
                "credited_at TIMESTAMPTZ"
                ")"
            )
        )


async def _ensure_dispute_columns(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure dispute columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'disputes'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    if "winner_id" not in columns:
        await conn.execute(text("ALTER TABLE disputes ADD COLUMN winner_id INTEGER"))


async def _ensure_review_unique_index(conn: AsyncConnection, dialect_name: str) -> None:
    """Handle ensure review unique index.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    await conn.execute(
        text(
            "DELETE FROM reviews WHERE id NOT IN ("
            "SELECT MAX(id) FROM reviews GROUP BY deal_id, author_id, target_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_reviews_deal_author_target "
            "ON reviews (deal_id, author_id, target_id)"
        )
    )
