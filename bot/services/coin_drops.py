# -*- coding: utf-8 -*-
"""GSNS coin drop helpers."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import CoinDrop, User, WalletTransaction


def roll_coin_drop_amount() -> int:
    """Return a weighted random amount for a GSNS coin bag."""
    roll = random.random()
    if roll < 0.85:
        return random.randint(1, 50)
    if roll < 0.95:
        return random.randint(51, 250)
    return random.randint(251, 500)


def apply_coin_drop_credit(
    *,
    user: User,
    amount: int,
    drop_id: int,
) -> WalletTransaction:
    """Apply coin drop reward to an existing user."""
    user.balance = (user.balance or 0) + amount
    return WalletTransaction(
        user_id=user.id,
        amount=amount,
        type="coin_drop",
        description="Мешок GSNS Coins",
        ref_type="coin_drop",
        ref_id=drop_id,
    )


async def grant_pending_coin_drops(
    bot,
    sessionmaker: async_sessionmaker,
    user_id: int,
) -> int:
    """Grant pending coin drops after user registers."""
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return 0

        result = await session.execute(
            select(CoinDrop)
            .where(
                CoinDrop.claimed_by == user_id,
                CoinDrop.credited.is_(False),
            )
            .order_by(CoinDrop.id.asc())
        )
        drops = result.scalars().all()
        if not drops:
            return 0

        total = 0
        now = datetime.now(timezone.utc)
        for drop in drops:
            if not drop.amount:
                continue
            total += int(drop.amount)
            drop.credited = True
            drop.credited_at = now
            session.add(
                apply_coin_drop_credit(
                    user=user,
                    amount=int(drop.amount),
                    drop_id=drop.id,
                )
            )
        if total > 0:
            await session.commit()

    if total > 0:
        try:
            await bot.send_message(
                user_id,
                (
                    f"🎁 Вам начислено {total} GSNS Coins за мешок в чате.\n"
                    "Спасибо за участие!"
                ),
            )
        except Exception:
            pass
    return total
