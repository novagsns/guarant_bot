"""Module for main functionality."""

from __future__ import annotations

import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select, text

from bot.config import load_settings
from bot.db import Base
from bot.db.models import Game
from bot.db.session import create_engine, create_sessionmaker
from bot.handlers import (
    admin_docs,
    ads,
    chat_moderation,
    deals,
    info,
    profile,
    scammers,
    services,
    staff,
    staff_panel,
    start,
    support,
)
from bot.middlewares import ActionLogMiddleware, AccessMiddleware, ContextMiddleware
from bot.services.daily_report import daily_report_loop
from bot.services.vip_jobs import vip_promotion_loop
from bot.services.weekly_rewards import weekly_reward_loop


async def _prepare_db(engine, sessionmaker, default_games: list[str]) -> None:
    """Handle prepare db.

    Args:
        engine: Value for engine.
        sessionmaker: Value for sessionmaker.
        default_games: Value for default_games.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_ads_media_columns(conn, engine.dialect.name)
        await _ensure_user_profile_columns(conn, engine.dialect.name)
        await _ensure_user_vip_columns(conn, engine.dialect.name)
        await _ensure_user_trust_columns(conn, engine.dialect.name)
        await _ensure_ads_moderation_columns(conn, engine.dialect.name)
        await _ensure_topup_columns(conn, engine.dialect.name)
        await _ensure_wallet_tx_columns(conn, engine.dialect.name)
        await _ensure_service_media_columns(conn, engine.dialect.name)
        await _ensure_support_ticket_columns(conn, engine.dialect.name)
        await _ensure_ad_account_columns(conn, engine.dialect.name)
        await _ensure_ads_promoted_columns(conn, engine.dialect.name)
        await _ensure_ads_kind_columns(conn, engine.dialect.name)
        await _ensure_deal_columns(conn, engine.dialect.name)
        await _ensure_dispute_columns(conn, engine.dialect.name)

    async with sessionmaker() as session:
        result = await session.execute(select(Game))
        if result.first() is None:
            session.add_all([Game(name=name) for name in default_games])
            await session.commit()


async def _ensure_ads_media_columns(conn, dialect_name: str) -> None:
    """Handle ensure ads media columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(ads)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_user_profile_columns(conn, dialect_name: str) -> None:
    """Handle ensure user profile columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(users)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_user_trust_columns(conn, dialect_name: str) -> None:
    """Handle ensure user trust columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(users)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_ads_moderation_columns(conn, dialect_name: str) -> None:
    """Handle ensure ads moderation columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(ads)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_user_vip_columns(conn, dialect_name: str) -> None:
    """Handle ensure user vip columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(users)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_topup_columns(conn, dialect_name: str) -> None:
    """Handle ensure topup columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(topups)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_wallet_tx_columns(conn, dialect_name: str) -> None:
    """Handle ensure wallet tx columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(wallet_transactions)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_service_media_columns(conn, dialect_name: str) -> None:
    """Handle ensure service media columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(services)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_support_ticket_columns(conn, dialect_name: str) -> None:
    """Handle ensure support ticket columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(support_tickets)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_ad_account_columns(conn, dialect_name: str) -> None:
    """Handle ensure ad account columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(ads)"))
        columns = {row[1] for row in result.fetchall()}
    else:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ads'"
            )
        )
        columns = {row[0] for row in result.fetchall()}

    if "account_id" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN account_id VARCHAR(64)"))


async def _ensure_ads_promoted_columns(conn, dialect_name: str) -> None:
    """Handle ensure ads promoted columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(ads)"))
        columns = {row[1] for row in result.fetchall()}
    else:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ads'"
            )
        )
        columns = {row[0] for row in result.fetchall()}

    if "promoted_at" not in columns:
        await conn.execute(text("ALTER TABLE ads ADD COLUMN promoted_at TIMESTAMP"))


async def _ensure_ads_kind_columns(conn, dialect_name: str) -> None:
    """Handle ensure ads kind columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(ads)"))
        columns = {row[1] for row in result.fetchall()}
    else:
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


async def _ensure_deal_columns(conn, dialect_name: str) -> None:
    """Handle ensure deal columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(deals)"))
        columns = {row[1] for row in result.fetchall()}
    else:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'deals'"
            )
        )
        columns = {row[0] for row in result.fetchall()}

    if "closed_at" not in columns:
        await conn.execute(text("ALTER TABLE deals ADD COLUMN closed_at TIMESTAMP"))


async def _ensure_dispute_columns(conn, dialect_name: str) -> None:
    """Handle ensure dispute columns.

    Args:
        conn: Value for conn.
        dialect_name: Value for dialect_name.
    """
    if dialect_name == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(disputes)"))
        columns = {row[1] for row in result.fetchall()}
    else:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'disputes'"
            )
        )
        columns = {row[0] for row in result.fetchall()}

    if "winner_id" not in columns:
        await conn.execute(text("ALTER TABLE disputes ADD COLUMN winner_id INTEGER"))


async def main() -> None:
    """Handle main."""
    settings = load_settings()

    if settings.database_url.startswith("sqlite"):
        os.makedirs("./data", exist_ok=True)

    engine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    await _prepare_db(engine, sessionmaker, settings.default_games)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(ContextMiddleware(sessionmaker, settings))
    dp.update.outer_middleware(ActionLogMiddleware(sessionmaker))
    dp.update.outer_middleware(AccessMiddleware(sessionmaker, settings))

    dp.include_router(start.router)
    dp.include_router(admin_docs.router)
    dp.include_router(chat_moderation.router)
    dp.include_router(ads.router)
    dp.include_router(deals.router)
    dp.include_router(info.router)
    dp.include_router(profile.router)
    dp.include_router(scammers.router)
    dp.include_router(services.router)
    dp.include_router(staff.router)
    dp.include_router(staff_panel.router)
    dp.include_router(support.router)

    asyncio.create_task(daily_report_loop(bot, sessionmaker, settings))
    asyncio.create_task(vip_promotion_loop(sessionmaker))
    asyncio.create_task(weekly_reward_loop(bot, sessionmaker, settings))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
