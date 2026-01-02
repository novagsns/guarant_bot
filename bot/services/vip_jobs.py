"""Module for vip jobs functionality."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import Ad, User
from bot.utils.vip import is_vip_until


def _next_promo_time(now: datetime) -> datetime:
    """Handle next promo time.

    Args:
        now: Value for now.

    Returns:
        Return value.
    """
    targets = [
        now.replace(hour=12, minute=0, second=0, microsecond=0),
        now.replace(hour=20, minute=0, second=0, microsecond=0),
    ]
    future = [t for t in targets if t > now]
    if future:
        return min(future)
    return targets[0] + timedelta(days=1)


async def _run_promotion(sessionmaker: async_sessionmaker) -> None:
    """Handle run promotion.

    Args:
        sessionmaker: Value for sessionmaker.
    """
    now = datetime.utcnow()
    async with sessionmaker() as session:
        result = await session.execute(
            select(Ad, User)
            .join(User, User.id == Ad.seller_id)
            .where(
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
            )
        )
        rows = result.all()
        for ad, user in rows:
            if is_vip_until(user.vip_until):
                ad.promoted_at = now
        await session.commit()


async def vip_promotion_loop(sessionmaker: async_sessionmaker) -> None:
    """Handle vip promotion loop.

    Args:
        sessionmaker: Value for sessionmaker.
    """
    try:
        tz = ZoneInfo("Europe/Moscow")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))
    while True:
        now = datetime.now(tz)
        target = _next_promo_time(now)
        await asyncio.sleep((target - now).total_seconds())
        await _run_promotion(sessionmaker)
