"""Module for main functionality."""

from __future__ import annotations

import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.config import load_settings
from bot.db.models import Game
from bot.db.schema import prepare_database
from bot.db.session import create_engine, create_sessionmaker
from bot.handlers import (
    admin_docs,
    ads,
    chat_moderation,
    coin_drop,
    deals,
    info,
    profile,
    scammers,
    services,
    staff,
    staff_panel,
    start,
    support,
    topic_activity,
)
from bot.handlers.moderation import commands as moderation_commands
from bot.middlewares import ActionLogMiddleware, AccessMiddleware, ContextMiddleware
from bot.services.daily_report import daily_report_loop
from bot.services.topic_activity import topic_activity_loop
from bot.services.vip_jobs import vip_promotion_loop
from bot.services.weekly_rewards import weekly_reward_loop
from bot.utils.send_queue import SendQueue


async def _ensure_default_games(sessionmaker, default_games: list[str]) -> None:
    """Ensure at least one game exists in the catalog.

    Args:
        sessionmaker: SQLAlchemy async session factory.
        default_games: List of game names to seed if empty.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(Game))
        if result.first() is None:
            session.add_all([Game(name=name) for name in default_games])
            await session.commit()


async def main() -> None:
    """Handle main."""
    settings = load_settings()

    if settings.database_url.startswith("sqlite"):
        os.makedirs("./data", exist_ok=True)

    engine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    await prepare_database(
        engine,
        settings.database_url,
        db_auto_backup=settings.db_auto_backup,
        db_backup_dir=settings.db_backup_dir,
        allow_destructive=settings.db_allow_destructive_migrations,
    )
    await _ensure_default_games(sessionmaker, settings.default_games)

    session = AiohttpSession(timeout=90)
    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    sender = SendQueue(
        delay_seconds=settings.send_delay_seconds,
        pause_every=settings.send_pause_every,
        pause_seconds=settings.send_pause_seconds,
        max_retries=settings.send_max_retries,
    )
    bot._raw_send_message = bot.send_message

    async def _queued_send_message(*args, **kwargs):
        return await sender.enqueue(args, kwargs)

    bot.send_message = _queued_send_message
    asyncio.create_task(sender.run(bot))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(ContextMiddleware(sessionmaker, settings))
    dp.update.outer_middleware(ActionLogMiddleware(sessionmaker))
    dp.update.outer_middleware(AccessMiddleware(sessionmaker, settings))

    dp.include_router(start.router)
    dp.include_router(admin_docs.router)
    dp.include_router(chat_moderation.router)
    dp.include_router(moderation_commands.router)
    dp.include_router(coin_drop.router)
    dp.include_router(ads.router)
    dp.include_router(deals.router)
    dp.include_router(info.router)
    dp.include_router(profile.router)
    dp.include_router(scammers.router)
    dp.include_router(services.router)
    dp.include_router(staff.router)
    dp.include_router(staff_panel.router)
    dp.include_router(support.router)
    dp.include_router(topic_activity.router)

    asyncio.create_task(daily_report_loop(bot, sessionmaker, settings))
    asyncio.create_task(vip_promotion_loop(sessionmaker))
    asyncio.create_task(weekly_reward_loop(bot, sessionmaker, settings))
    asyncio.create_task(topic_activity_loop(bot, sessionmaker))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
