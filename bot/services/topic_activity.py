# -*- coding: utf-8 -*-
"""Topic activity leaderboard and rewards."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import html

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import (
    TopicActivityMeta,
    TopicActivityReward,
    TopicActivityStat,
    User,
    WalletTransaction,
)

try:
    from zoneinfo import ZoneInfo

    LEADERBOARD_TZ = ZoneInfo("Europe/Moscow")
except Exception:  # pragma: no cover - fallback for missing tz data
    LEADERBOARD_TZ = timezone(timedelta(hours=3))

TARGET_CHAT_ID = -1001582810534
TARGET_TOPIC_ID = 390145
UPDATE_INTERVAL_SECONDS = 2
COUNT_COOLDOWN_SECONDS = 1
MAX_LEADERBOARD = 5
WEEKLY_REWARD_AMOUNT = 500


def _mask_user_id(user_id: int) -> str:
    raw = str(user_id)
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{'*' * (len(raw) - 4)}{raw[-4:]}"


def _format_user_label(username: str | None, full_name: str | None, user_id: int) -> str:
    if username:
        return f"@{html.escape(username)}"
    name = full_name or "Участник"
    return f"{html.escape(name)} • id:{_mask_user_id(user_id)}"


def _period_label(period_start: datetime | None) -> str:
    if not period_start:
        return "текущая неделя"
    local_dt = period_start.astimezone(LEADERBOARD_TZ)
    return f"с {local_dt.strftime('%d.%m.%Y')}"


def _format_update_time(updated_at: datetime) -> str:
    local_dt = updated_at.astimezone(LEADERBOARD_TZ)
    return local_dt.strftime("%H:%M")


def _build_leaderboard_text(
    stats: list[TopicActivityStat],
    period_start: datetime | None,
    updated_at: datetime,
) -> str:
    lines = [
        "🏆 <b>Топ активности в теме</b>",
        f"Период: {_period_label(period_start)}",
        f"Обновлено: {_format_update_time(updated_at)}",
        "",
    ]
    if stats:
        for idx, row in enumerate(stats, start=1):
            label = _format_user_label(row.username, row.full_name, row.user_id)
            lines.append(f"{idx}. {label} — {row.message_count} сообщений")
    else:
        lines.append("Пока тишина — будь первым и забери лидерство!")
    lines += [
        "",
        f"🎁 По пятницам лидер получает {WEEKLY_REWARD_AMOUNT} GSNS Coins",
        "⏱ Обновление каждые несколько секунд",
    ]
    return "\n".join(lines)


async def _get_or_create_meta(session) -> TopicActivityMeta:
    result = await session.execute(
        select(TopicActivityMeta).where(
            TopicActivityMeta.chat_id == TARGET_CHAT_ID,
            TopicActivityMeta.topic_id == TARGET_TOPIC_ID,
        )
    )
    meta = result.scalar_one_or_none()
    if not meta:
        meta = TopicActivityMeta(
            chat_id=TARGET_CHAT_ID,
            topic_id=TARGET_TOPIC_ID,
            period_start=datetime.now(timezone.utc),
        )
        session.add(meta)
        await session.commit()
        await session.refresh(meta)
    if not meta.period_start:
        meta.period_start = datetime.now(timezone.utc)
        await session.commit()
    return meta


async def record_topic_message(
    sessionmaker: async_sessionmaker,
    *,
    user_id: int,
    username: str | None,
    full_name: str | None,
) -> bool:
    """Record a message for leaderboard stats with cooldown."""
    now = datetime.now(timezone.utc)
    async with sessionmaker() as session:
        await _get_or_create_meta(session)
        result = await session.execute(
            select(TopicActivityStat).where(
                TopicActivityStat.chat_id == TARGET_CHAT_ID,
                TopicActivityStat.topic_id == TARGET_TOPIC_ID,
                TopicActivityStat.user_id == user_id,
            )
        )
        stat = result.scalar_one_or_none()
        if stat and stat.last_counted_at:
            delta = (now - stat.last_counted_at).total_seconds()
            if delta < COUNT_COOLDOWN_SECONDS:
                return False
        if stat:
            stat.message_count = (stat.message_count or 0) + 1
            stat.last_counted_at = now
            stat.username = username
            stat.full_name = full_name
            stat.updated_at = now
        else:
            stat = TopicActivityStat(
                chat_id=TARGET_CHAT_ID,
                topic_id=TARGET_TOPIC_ID,
                user_id=user_id,
                username=username,
                full_name=full_name,
                message_count=1,
                last_counted_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(stat)
        await session.commit()
    return True


async def update_pinned_leaderboard(bot, sessionmaker: async_sessionmaker) -> None:
    now_utc = datetime.now(timezone.utc)
    async with sessionmaker() as session:
        meta = await _get_or_create_meta(session)
        result = await session.execute(
            select(TopicActivityStat)
            .where(
                TopicActivityStat.chat_id == TARGET_CHAT_ID,
                TopicActivityStat.topic_id == TARGET_TOPIC_ID,
            )
            .order_by(
                TopicActivityStat.message_count.desc(),
                TopicActivityStat.updated_at.desc().nullslast(),
            )
            .limit(MAX_LEADERBOARD)
        )
        stats = result.scalars().all()
        text = _build_leaderboard_text(stats, meta.period_start, now_utc)
        meta.updated_at = now_utc
        await session.commit()

    if meta.pinned_message_id:
        try:
            await bot.edit_message_text(
                chat_id=TARGET_CHAT_ID,
                message_id=meta.pinned_message_id,
                text=text,
                parse_mode="HTML",
            )
            try:
                await bot.pin_chat_message(
                    TARGET_CHAT_ID,
                    meta.pinned_message_id,
                    message_thread_id=TARGET_TOPIC_ID,
                )
            except Exception:
                pass
            return
        except TelegramRetryAfter:
            return
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if "message is not modified" in message:
                try:
                    await bot.pin_chat_message(
                        TARGET_CHAT_ID,
                        meta.pinned_message_id,
                        message_thread_id=TARGET_TOPIC_ID,
                    )
                except Exception:
                    pass
                return
            recreate_errors = (
                "message to edit not found",
                "message can't be edited",
                "message id is not specified",
            )
            if not any(err in message for err in recreate_errors):
                return
        except Exception:
            return

    try:
        sent = await bot.send_message(
            TARGET_CHAT_ID,
            text,
            message_thread_id=TARGET_TOPIC_ID,
            parse_mode="HTML",
        )
    except Exception:
        return

    try:
        await bot.pin_chat_message(
            TARGET_CHAT_ID,
            sent.message_id,
            message_thread_id=TARGET_TOPIC_ID,
        )
    except Exception:
        pass

    async with sessionmaker() as session:
        await session.execute(
            update(TopicActivityMeta)
            .where(
                TopicActivityMeta.chat_id == TARGET_CHAT_ID,
                TopicActivityMeta.topic_id == TARGET_TOPIC_ID,
            )
            .values(pinned_message_id=sent.message_id)
        )
        await session.commit()


async def maybe_update_leaderboard(
    bot,
    sessionmaker: async_sessionmaker,
) -> None:
    """Update the leaderboard if the interval has passed."""
    now = datetime.now(timezone.utc)
    async with sessionmaker() as session:
        meta = await _get_or_create_meta(session)
        if meta.updated_at:
            delta = (now - meta.updated_at).total_seconds()
            if delta < UPDATE_INTERVAL_SECONDS:
                return
    await update_pinned_leaderboard(bot, sessionmaker)


async def _maybe_award_weekly(bot, sessionmaker: async_sessionmaker) -> None:
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(LEADERBOARD_TZ)
    if now_local.weekday() != 4:
        return

    async with sessionmaker() as session:
        meta = await _get_or_create_meta(session)
        if meta.last_reward_at:
            last_local = meta.last_reward_at.astimezone(LEADERBOARD_TZ)
            if last_local.date() == now_local.date():
                return

        result = await session.execute(
            select(TopicActivityStat)
            .where(
                TopicActivityStat.chat_id == TARGET_CHAT_ID,
                TopicActivityStat.topic_id == TARGET_TOPIC_ID,
            )
            .order_by(
                TopicActivityStat.message_count.desc(),
                TopicActivityStat.updated_at.desc().nullslast(),
            )
            .limit(1)
        )
        winner = result.scalar_one_or_none()
        if not winner:
            return

        reward = TopicActivityReward(
            chat_id=TARGET_CHAT_ID,
            topic_id=TARGET_TOPIC_ID,
            user_id=winner.user_id,
            amount=WEEKLY_REWARD_AMOUNT,
            status="pending",
            period_start=meta.period_start,
        )
        session.add(reward)
        await session.flush()

        result = await session.execute(
            select(User).where(User.id == winner.user_id)
        )
        user = result.scalar_one_or_none()
        user_exists = user is not None
        if user:
            user.balance = (user.balance or 0) + WEEKLY_REWARD_AMOUNT
            reward.status = "granted"
            reward.granted_at = now_utc
            session.add(
                WalletTransaction(
                    user_id=user.id,
                    amount=WEEKLY_REWARD_AMOUNT,
                    type="topic_leader",
                    description="Награда за топ активности",
                    ref_type="topic_activity",
                    ref_id=reward.id,
                )
            )

        meta.last_reward_at = now_utc
        meta.period_start = now_utc
        await session.execute(
            delete(TopicActivityStat).where(
                TopicActivityStat.chat_id == TARGET_CHAT_ID,
                TopicActivityStat.topic_id == TARGET_TOPIC_ID,
            )
        )
        await session.commit()

    winner_label = _format_user_label(
        winner.username, winner.full_name, winner.user_id
    )
    if user_exists:
        claim_text = "Награда уже начислена на баланс."
    else:
        claim_text = "Если ты ещё не запускал бота — нажми /start и забери награду."

    text = (
        "🎉 <b>Итоги недели в теме</b>\n"
        f"Победитель: {winner_label}\n"
        f"Выигрыш: <b>{WEEKLY_REWARD_AMOUNT} GSNS Coins</b>\n\n"
        f"{claim_text}"
    )
    try:
        await bot.send_message(
            TARGET_CHAT_ID,
            text,
            message_thread_id=TARGET_TOPIC_ID,
            parse_mode="HTML",
        )
    except Exception:
        pass


async def grant_pending_topic_rewards(
    bot,
    sessionmaker: async_sessionmaker,
    user_id: int,
) -> int:
    """Grant pending weekly topic rewards after /start."""
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return 0

        result = await session.execute(
            select(TopicActivityReward)
            .where(
                TopicActivityReward.user_id == user_id,
                TopicActivityReward.status == "pending",
            )
            .order_by(TopicActivityReward.id.asc())
        )
        rewards = result.scalars().all()
        if not rewards:
            return 0

        total = 0
        now = datetime.now(timezone.utc)
        for reward in rewards:
            total += reward.amount
            reward.status = "granted"
            reward.granted_at = now
            session.add(
                WalletTransaction(
                    user_id=user.id,
                    amount=reward.amount,
                    type="topic_leader",
                    description="Награда за топ активности",
                    ref_type="topic_activity",
                    ref_id=reward.id,
                )
            )
        user.balance = (user.balance or 0) + total
        await session.commit()

    try:
        await bot.send_message(
            user_id,
            f"🏆 Награда за активность в теме начислена: {total} GSNS Coins.",
        )
    except Exception:
        pass
    return total


async def topic_activity_loop(bot, sessionmaker: async_sessionmaker) -> None:
    """Periodic leaderboard update + weekly rewards."""
    while True:
        try:
            await update_pinned_leaderboard(bot, sessionmaker)
            await _maybe_award_weekly(bot, sessionmaker)
        except Exception:
            pass
        await asyncio.sleep(UPDATE_INTERVAL_SECONDS)
