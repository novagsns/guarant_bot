"""Module for trade bonus calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select

from bot.db.models import Deal

DEAL_MIN_AMOUNT = Decimal("2500")

LEVEL_DEFINITIONS = (
    (3, 25, "GSNS ELITE"),
    (2, 15, "GSNS PRO"),
    (1, 10, "GSNS Trader"),
)


@dataclass
class TradeLevel:
    """Represent a user's trade bonus level."""

    tier: int
    prefix: str | None
    deals_count: int

    def is_active(self) -> bool:
        """Determine whether the user has reached a bonus tier."""
        return self.tier > 0


def _determine_tier(count: int) -> int:
    """Return the highest tier that matches the count."""

    for tier, threshold, _prefix in LEVEL_DEFINITIONS:
        if count >= threshold:
            return tier
    return 0


def _level_info_by_tier(tier: int) -> tuple[int, str] | None:
    """Return threshold and prefix for the given tier."""

    for level, threshold, prefix in LEVEL_DEFINITIONS:
        if level == tier:
            return threshold, prefix
    return None


async def get_trade_level(session, user_id: int) -> TradeLevel:
    """Return the user's current trade bonus level."""

    result = await session.execute(
        select(func.count(Deal.id))
        .where(
            Deal.status == "closed",
            Deal.price.is_not(None),
            Deal.price >= DEAL_MIN_AMOUNT,
            or_(Deal.buyer_id == user_id, Deal.seller_id == user_id),
        )
    )
    deals_count = result.scalar_one() or 0
    tier = _determine_tier(deals_count)
    prefix = _level_info_by_tier(tier)[1] if tier else None
    return TradeLevel(tier=tier, prefix=prefix, deals_count=deals_count)


def next_tier_info(level: TradeLevel) -> tuple[int, str] | None:
    """Return the next tier target after the current level."""

    sorted_levels = sorted(LEVEL_DEFINITIONS, key=lambda item: item[0])
    for tier, threshold, prefix in sorted_levels:
        if tier > level.tier:
            return threshold, prefix
    return None
