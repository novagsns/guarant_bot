"""Module for trust functionality."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from bot.db.models import (
    Deal,
    Dispute,
    ModerationCase,
    TrustEvent,
    TrustState,
    User,
)

TRUST_MIN = 0
TRUST_MAX = 100
NEW_ACCOUNT_DAYS = 30


def _month_key(dt: datetime) -> str:
    """Handle month key.

    Args:
        dt: Value for dt.

    Returns:
        Return value.
    """
    return dt.strftime("%Y-%m")


def _cap_for_user(user: User) -> int:
    """Handle cap for user.

    Args:
        user: Value for user.

    Returns:
        Return value.
    """
    created_at = user.created_at
    if not created_at:
        return TRUST_MAX
    is_naive = (
        created_at.tzinfo is None or created_at.tzinfo.utcoffset(created_at) is None
    )
    now = datetime.utcnow() if is_naive else datetime.now(timezone.utc)
    if created_at >= now - timedelta(days=NEW_ACCOUNT_DAYS):
        return 70
    return TRUST_MAX


async def get_trust_state(session, user_id: int) -> TrustState:
    """Get trust state.

    Args:
        session: Value for session.
        user_id: Value for user_id.

    Returns:
        Return value.
    """
    result = await session.execute(
        select(TrustState).where(TrustState.user_id == user_id)
    )
    state = result.scalar_one_or_none()
    if state:
        return state
    state = TrustState(user_id=user_id, score=0, frozen=False, cap=TRUST_MAX)
    session.add(state)
    await session.commit()
    return state


async def get_trust_score(session, user_id: int) -> int:
    """Get trust score.

    Args:
        session: Value for session.
        user_id: Value for user_id.

    Returns:
        Return value.
    """
    state = await get_trust_state(session, user_id)
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        cap = _cap_for_user(user)
        if state.cap != cap:
            state.cap = cap
            await session.commit()
    return min(state.score, state.cap)


async def apply_trust_event(
    session,
    user_id: int,
    event_type: str,
    delta: int,
    reason: str,
    *,
    ref_type: str | None = None,
    ref_id: int | None = None,
    allow_duplicate: bool = False,
) -> TrustEvent:
    """Apply trust event.

    Args:
        session: Value for session.
        user_id: Value for user_id.
        event_type: Value for event_type.
        delta: Value for delta.
        reason: Value for reason.
        ref_type: Value for ref_type.
        ref_id: Value for ref_id.
        allow_duplicate: Value for allow_duplicate.

    Returns:
        Return value.
    """
    if ref_type and ref_id and not allow_duplicate:
        exists = await session.execute(
            select(TrustEvent.id).where(
                TrustEvent.user_id == user_id,
                TrustEvent.event_type == event_type,
                TrustEvent.ref_type == ref_type,
                TrustEvent.ref_id == ref_id,
                TrustEvent.reversed.is_(False),
            )
        )
        if exists.scalar_one_or_none():
            return TrustEvent(
                user_id=user_id,
                event_type=event_type,
                delta=0,
                reason=reason,
                ref_type=ref_type,
                ref_id=ref_id,
                applied=False,
                reversed=True,
            )

    state = await get_trust_state(session, user_id)
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    cap = _cap_for_user(user) if user else TRUST_MAX
    state.cap = cap

    applied = not state.frozen
    if applied:
        new_score = max(TRUST_MIN, min(TRUST_MAX, state.score + delta))
        if new_score > state.cap:
            new_score = state.cap
        state.score = new_score

    event = TrustEvent(
        user_id=user_id,
        event_type=event_type,
        delta=delta,
        reason=reason,
        ref_type=ref_type,
        ref_id=ref_id,
        applied=applied,
        reversed=False,
    )
    session.add(event)
    await session.commit()
    return event


async def rollback_trust_event(session, event_id: int) -> bool:
    """Handle rollback trust event.

    Args:
        session: Value for session.
        event_id: Value for event_id.

    Returns:
        Return value.
    """
    result = await session.execute(select(TrustEvent).where(TrustEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event or event.reversed:
        return False

    state = await get_trust_state(session, event.user_id)
    if event.applied and not state.frozen:
        new_score = max(TRUST_MIN, min(TRUST_MAX, state.score - event.delta))
        if new_score > state.cap:
            new_score = state.cap
        state.score = new_score
    event.reversed = True
    await session.commit()
    return True


async def set_trust_frozen(session, user_id: int, frozen: bool) -> None:
    """Set trust frozen.

    Args:
        session: Value for session.
        user_id: Value for user_id.
        frozen: Value for frozen.
    """
    state = await get_trust_state(session, user_id)
    state.frozen = frozen
    await session.commit()


async def apply_monthly_activity_bonus(session, user_id: int) -> None:
    """Apply monthly activity bonus.

    Args:
        session: Value for session.
        user_id: Value for user_id.
    """
    state = await get_trust_state(session, user_id)
    now = datetime.now(timezone.utc)
    current_month = _month_key(now)
    if state.last_activity_month == current_month:
        return
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    result = await session.execute(
        select(func.count(ModerationCase.id)).where(
            ModerationCase.user_id == user_id,
            ModerationCase.created_at >= month_start,
        )
    )
    has_cases = (result.scalar_one() or 0) > 0
    if not has_cases:
        await apply_trust_event(
            session,
            user_id,
            "monthly_clean",
            2,
            "Активность без нарушений (месяц)",
            ref_type="month",
            ref_id=int(current_month.replace("-", "")),
        )
    state.last_activity_month = current_month
    await session.commit()


async def apply_deal_no_dispute_bonus(session, user_id: int) -> None:
    """Apply deal no dispute bonus.

    Args:
        session: Value for session.
        user_id: Value for user_id.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    result = await session.execute(
        select(Deal).where(
            Deal.status == "closed",
            Deal.closed_at.isnot(None),
            Deal.closed_at <= cutoff,
            (Deal.buyer_id == user_id) | (Deal.seller_id == user_id),
        )
    )
    deals = result.scalars().all()
    for deal in deals:
        dispute = await session.execute(
            select(Dispute.id).where(Dispute.deal_id == deal.id)
        )
        if dispute.scalar_one_or_none():
            continue
        await apply_trust_event(
            session,
            user_id,
            "deal_no_dispute",
            3,
            "Сделка без споров 30 дней",
            ref_type="deal",
            ref_id=deal.id,
        )


async def get_trust_factors(session, user_id: int, limit: int = 3) -> list[str]:
    """Get trust factors.

    Args:
        session: Value for session.
        user_id: Value for user_id.
        limit: Value for limit.

    Returns:
        Return value.
    """
    result = await session.execute(
        select(TrustEvent)
        .where(
            TrustEvent.user_id == user_id,
            TrustEvent.delta < 0,
            TrustEvent.reversed.is_(False),
        )
        .order_by(TrustEvent.id.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [e.reason for e in events if e.reason]
