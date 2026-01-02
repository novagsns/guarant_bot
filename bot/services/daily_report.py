"""Module for daily report functionality."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    Ad,
    Deal,
    Game,
    ModerationChat,
    ModerationMemberEvent,
    Scammer,
    TopUp,
    User,
)


def _day_bounds_msk() -> tuple[datetime, datetime]:
    """Handle day bounds msk.

    Returns:
        Return value.
    """
    # Use Moscow timezone to align reporting window with business expectations.
    try:
        tz = ZoneInfo("Europe/Moscow")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))
    now = datetime.now(tz)
    start = datetime(now.year, now.month, now.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def _to_query_bounds(settings: Settings) -> tuple[datetime, datetime]:
    """Handle to query bounds.

    Args:
        settings: Value for settings.

    Returns:
        Return value.
    """
    # SQLite stores naive timestamps, so normalize bounds accordingly.
    start, end = _day_bounds_msk()
    if settings.database_url.startswith("sqlite"):
        return start.replace(tzinfo=None), end.replace(tzinfo=None)
    return start, end


async def send_daily_report(
    bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Send daily report.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    start, end = _to_query_bounds(settings)
    async with sessionmaker() as session:
        deals_total = await session.scalar(
            select(func.count())
            .select_from(Deal)
            .where(Deal.created_at >= start, Deal.created_at < end)
        )
        deals_closed = await session.scalar(
            select(func.count())
            .select_from(Deal)
            .where(
                Deal.created_at >= start,
                Deal.created_at < end,
                Deal.status == "closed",
            )
        )
        deals_turnover = await session.scalar(
            select(func.coalesce(func.sum(Deal.price), 0)).where(
                Deal.created_at >= start,
                Deal.created_at < end,
                Deal.status == "closed",
            )
        )
        users_count = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.created_at >= start, User.created_at < end)
        )
        scammers_count = await session.scalar(
            select(func.count())
            .select_from(Scammer)
            .where(Scammer.created_at >= start, Scammer.created_at < end)
        )
        topups_count = await session.scalar(
            select(func.count())
            .select_from(TopUp)
            .where(
                TopUp.created_at >= start,
                TopUp.created_at < end,
                TopUp.status == "approved",
            )
        )
        topups_sum = await session.scalar(
            select(func.coalesce(func.sum(TopUp.amount), 0)).where(
                TopUp.created_at >= start,
                TopUp.created_at < end,
                TopUp.status == "approved",
            )
        )
        topups_rub = await session.scalar(
            select(func.coalesce(func.sum(TopUp.amount_rub), 0)).where(
                TopUp.created_at >= start,
                TopUp.created_at < end,
                TopUp.status == "approved",
            )
        )
        deals_fee = await session.scalar(
            select(func.coalesce(func.sum(Deal.fee), 0)).where(
                Deal.created_at >= start,
                Deal.created_at < end,
                Deal.status == "closed",
            )
        )
        result = await session.execute(
            select(Game.name, func.count(Deal.id))
            .join(Ad, Ad.game_id == Game.id)
            .join(Deal, Deal.ad_id == Ad.id)
            .where(Deal.created_at >= start, Deal.created_at < end)
            .group_by(Game.name)
            .order_by(func.count(Deal.id).desc())
            .limit(5)
        )
        top_games = result.all()
        result = await session.execute(
            select(Game.name, func.coalesce(func.sum(Deal.price), 0))
            .join(Ad, Ad.game_id == Game.id)
            .join(Deal, Deal.ad_id == Ad.id)
            .where(
                Deal.created_at >= start,
                Deal.created_at < end,
                Deal.status == "closed",
            )
            .group_by(Game.name)
            .order_by(func.sum(Deal.price).desc())
            .limit(5)
        )
        games_turnover = result.all()
        result = await session.execute(
            select(
                Deal.buyer_id,
                func.count(Deal.id),
                func.coalesce(func.sum(Deal.price), 0),
            )
            .where(
                Deal.created_at >= start,
                Deal.created_at < end,
                Deal.status == "closed",
            )
            .group_by(Deal.buyer_id)
            .order_by(func.count(Deal.id).desc())
            .limit(5)
        )
        top_buyers = result.all()
        result = await session.execute(
            select(
                Deal.seller_id,
                func.count(Deal.id),
                func.coalesce(func.sum(Deal.price), 0),
            )
            .where(
                Deal.created_at >= start,
                Deal.created_at < end,
                Deal.status == "closed",
            )
            .group_by(Deal.seller_id)
            .order_by(func.count(Deal.id).desc())
            .limit(5)
        )
        top_sellers = result.all()
        result = await session.execute(
            select(
                Deal.guarantee_id,
                func.count(Deal.id),
                func.coalesce(func.sum(Deal.price), 0),
                func.coalesce(func.sum(Deal.fee), 0),
            )
            .where(
                Deal.created_at >= start,
                Deal.created_at < end,
                Deal.guarantee_id.isnot(None),
            )
            .group_by(Deal.guarantee_id)
            .order_by(func.count(Deal.id).desc())
            .limit(5)
        )
        guarantor_stats = result.all()
        result = await session.execute(
            select(ModerationChat.chat_id, ModerationChat.title).where(
                ModerationChat.active.is_(True)
            )
        )
        moderated_chats = result.all()
        member_events: dict[int, dict[str, int]] = {}
        if moderated_chats:
            # Aggregate join/leave stats per moderated chat for the report window.
            chat_ids = [chat_id for chat_id, _ in moderated_chats]
            result = await session.execute(
                select(
                    ModerationMemberEvent.chat_id,
                    ModerationMemberEvent.event_type,
                    func.count(ModerationMemberEvent.id),
                )
                .where(
                    ModerationMemberEvent.chat_id.in_(chat_ids),
                    ModerationMemberEvent.created_at >= start,
                    ModerationMemberEvent.created_at < end,
                )
                .group_by(
                    ModerationMemberEvent.chat_id,
                    ModerationMemberEvent.event_type,
                )
            )
            for chat_id, event_type, count in result.all():
                member_events.setdefault(chat_id, {"join": 0, "leave": 0})
                if event_type in member_events[chat_id]:
                    member_events[chat_id][event_type] = count

    admin_chat_id = settings.admin_chat_id
    if admin_chat_id == 0:
        return
    avg_check = 0
    if deals_closed:
        avg_check = (deals_turnover or 0) / deals_closed
    games_line = "—"
    if top_games:
        games_line = ", ".join(f"{name} ({count})" for name, count in top_games)
    games_turnover_line = "—"
    if games_turnover:
        games_turnover_line = ", ".join(
            f"{name}: {turnover} ₽" for name, turnover in games_turnover
        )
    top_buyers_line = "—"
    if top_buyers:
        top_buyers_line = ", ".join(
            f"{buyer_id} ({cnt}, {sum_turn} ₽)"
            for buyer_id, cnt, sum_turn in top_buyers
        )
    top_sellers_line = "—"
    if top_sellers:
        top_sellers_line = ", ".join(
            f"{seller_id} ({cnt}, {sum_turn} ₽)"
            for seller_id, cnt, sum_turn in top_sellers
        )
    guarantors_line = "—"
    if guarantor_stats:
        guarantors_line = ", ".join(
            f"{gid} ({cnt} сделок, {sum_turn} ₽, комиссий {sum_fee} ₽)"
            for gid, cnt, sum_turn, sum_fee in guarantor_stats
        )
    member_events_line = "-"
    if moderated_chats:
        # Render per-chat join/leave lines in a stable order.
        lines = []
        for mod_chat_id, title in moderated_chats:
            stats = member_events.get(mod_chat_id, {"join": 0, "leave": 0})
            name = title or str(mod_chat_id)
            lines.append(f"{name}: +{stats['join']}/-{stats['leave']}")
        member_events_line = "\n".join(lines)
    text = (
        "Ежедневный отчет GSNS\n"
        f"Сделки всего: {deals_total or 0}\n"
        f"Сделки закрыто: {deals_closed or 0}\n"
        f"Оборот сделок: {deals_turnover or 0} ₽\n"
        f"Средний чек: {avg_check:.2f} ₽\n"
        f"Заработок со сделок: {deals_fee or 0} ₽\n"
        f"Заработок с доната: {topups_rub or 0} ₽\n"
        f"Новых пользователей: {users_count or 0}\n"
        f"Пополнений коинов: {topups_count or 0} (сумма {topups_sum or 0} Coins)\n"
        f"Новых скамеров в базе: {scammers_count or 0}\n"
        f"Входы/выходы по группам:\n{member_events_line}\n"
        f"Топ-игры (сделки): {games_line}\n"
        f"Оборот по играм: {games_turnover_line}\n"
        f"Топ-покупатели: {top_buyers_line}\n"
        f"Топ-продавцы: {top_sellers_line}\n"
        f"Гаранты: {guarantors_line}"
    )
    # Daily report is always sent to ADMIN_CHAT_ID from settings.
    await bot.send_message(admin_chat_id, text)


async def daily_report_loop(
    bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle daily report loop.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    try:
        tz = ZoneInfo("Europe/Moscow")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))
    while True:
        now = datetime.now(tz)
        target = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        await send_daily_report(bot, sessionmaker, settings)
