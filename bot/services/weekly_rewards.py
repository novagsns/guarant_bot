"""Module for weekly rewards functionality."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import User, UserAction, WeeklyReward
from bot.utils.admin_target import get_admin_target


async def weekly_reward_loop(
    bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle weekly reward loop.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    while True:
        try:
            await _process_weekly_reward(bot, sessionmaker, settings)
        except Exception:
            pass
        await asyncio.sleep(3600)


async def _process_weekly_reward(
    bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle process weekly reward.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    now = datetime.now()
    if now.weekday() != 6:
        return

    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async with sessionmaker() as session:
        existing = await session.execute(
            select(WeeklyReward).where(WeeklyReward.week_start == week_start)
        )
        if existing.scalar_one_or_none():
            return

        since = now - timedelta(days=7)
        result = await session.execute(
            select(UserAction.user_id, func.count(UserAction.id).label("cnt"))
            .join(User, User.id == UserAction.user_id)
            .where(
                UserAction.created_at >= since,
                User.role == "user",
            )
            .group_by(UserAction.user_id)
            .order_by(func.count(UserAction.id).desc())
            .limit(1)
        )
        top = result.first()
        if not top:
            return
        user_id = top[0]
        reward = WeeklyReward(
            week_start=week_start,
            user_id=user_id,
            amount=500,
            status="pending",
        )
        session.add(reward)
        await session.commit()

    await _try_grant_reward(bot, sessionmaker, settings, user_id)


async def _try_grant_reward(
    bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    user_id: int,
) -> None:
    """Handle try grant reward.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
        user_id: Value for user_id.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(WeeklyReward)
            .where(
                WeeklyReward.user_id == user_id,
                WeeklyReward.status == "pending",
            )
            .order_by(WeeklyReward.id.desc())
        )
        reward = result.scalar_one_or_none()
        if not reward:
            return

        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return

        try:
            await bot.send_message(
                user_id,
                "🎉 Вы самый активный пользователь недели. Начислено 500 GSNS Coins.",
            )
        except Exception:
            chat_id, topic_id = get_admin_target(settings)
            if chat_id != 0:
                await bot.send_message(
                    chat_id,
                    (
                        "⚠️ Награда недели не вручена автоматически.\n"
                        f"Пользователь: {user_id}\n"
                        "Статус: ожидает выдачи при /start."
                    ),
                    message_thread_id=topic_id,
                )
            return

        user.balance = (user.balance or 0) + reward.amount
        reward.status = "granted"
        await session.commit()


async def grant_pending_rewards(
    bot,
    sessionmaker: async_sessionmaker,
    user_id: int,
) -> bool:
    """Handle grant pending rewards.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        user_id: Value for user_id.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(WeeklyReward)
            .where(
                WeeklyReward.user_id == user_id,
                WeeklyReward.status == "pending",
            )
            .order_by(WeeklyReward.id.desc())
        )
        reward = result.scalar_one_or_none()
        if not reward:
            return False

        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return False

        user.balance = (user.balance or 0) + reward.amount
        reward.status = "granted"
        await session.commit()

    try:
        await bot.send_message(
            user_id,
            "🎉 Награда недели начислена: 500 GSNS Coins.",
        )
    except Exception:
        pass
    return True
