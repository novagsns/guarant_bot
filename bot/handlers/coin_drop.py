# -*- coding: utf-8 -*-
"""GSNS coin drop handlers."""

from __future__ import annotations

from datetime import datetime, timezone
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import CoinDrop, User
from bot.services.coin_drops import apply_coin_drop_credit, roll_coin_drop_amount
from bot.utils.roles import is_owner, is_staff

router = Router()

TARGET_CHAT_ID = -1001582810534
TARGET_TOPIC_ID = 390145
CLAIM_PREFIX = "gold_drop:"

DROP_TEXT = (
    "🎁 <b>Золотой мешок GSNS Coins!</b>\n"
    "Внутри случайно от 1 до 500 монет.\n"
    "Успеет только один — жми кнопку и забирай добычу!"
)
DROP_BUTTON_TEXT = "👜 Подобрать мешок"


def _format_winner_label(user) -> str:
    username = getattr(user, "username", None)
    if username:
        return f"@{html.escape(username)}"
    full_name = getattr(user, "full_name", None) or getattr(user, "first_name", None)
    return html.escape(full_name) if full_name else "кто-то"


@router.message(Command("gold"))
async def gold_drop(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Create a GSNS coin bag drop."""
    if not message.from_user:
        return

    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Нет доступа. Откройте бота и нажмите /start.")
            return
        if not (is_staff(user.role) or is_owner(user.role, settings.owner_ids, user.id)):
            await message.answer("Нет доступа.")
            return

        drop = CoinDrop(
            chat_id=TARGET_CHAT_ID,
            topic_id=TARGET_TOPIC_ID,
            created_by=user.id,
        )
        session.add(drop)
        await session.commit()
        await session.refresh(drop)

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=DROP_BUTTON_TEXT, callback_data=f"{CLAIM_PREFIX}{drop.id}")]
        ]
    )
    try:
        sent = await message.bot.send_message(
            TARGET_CHAT_ID,
            DROP_TEXT,
            message_thread_id=TARGET_TOPIC_ID,
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception:
        async with sessionmaker() as session:
            await session.execute(delete(CoinDrop).where(CoinDrop.id == drop.id))
            await session.commit()
        await message.answer("Не удалось отправить мешок.")
        return

    async with sessionmaker() as session:
        await session.execute(
            update(CoinDrop)
            .where(CoinDrop.id == drop.id)
            .values(message_id=sent.message_id)
        )
        await session.commit()

    await message.answer("Мешок отправлен в топик.")


@router.callback_query(F.data.startswith(CLAIM_PREFIX))
async def claim_gold_drop(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Claim a GSNS coin bag drop."""
    if not callback.from_user:
        return

    try:
        drop_id = int(callback.data.split(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer("Мешок не найден.", show_alert=True)
        return

    amount = roll_coin_drop_amount()
    now = datetime.now(timezone.utc)
    winner_label = _format_winner_label(callback.from_user)
    stored_username = getattr(callback.from_user, "username", None) or None

    async with sessionmaker() as session:
        result = await session.execute(select(CoinDrop).where(CoinDrop.id == drop_id))
        drop = result.scalar_one_or_none()
        if not drop:
            await callback.answer("Мешок уже исчез.", show_alert=True)
            return

        if drop.chat_id != TARGET_CHAT_ID:
            await callback.answer("Мешок не найден.", show_alert=True)
            return

        if drop.claimed_by:
            if drop.claimed_by == callback.from_user.id:
                await callback.answer("Ты уже подобрал этот мешок.", show_alert=True)
            else:
                await callback.answer("Уже подобрали!", show_alert=True)
            return

        result = await session.execute(
            update(CoinDrop)
            .where(CoinDrop.id == drop_id, CoinDrop.claimed_by.is_(None))
            .values(
                claimed_by=callback.from_user.id,
                claimed_username=stored_username,
                claimed_at=now,
                amount=amount,
            )
        )
        if result.rowcount != 1:
            await callback.answer("Уже подобрали!", show_alert=True)
            return

        result = await session.execute(select(User).where(User.id == callback.from_user.id))
        user = result.scalar_one_or_none()
        if user:
            session.add(
                apply_coin_drop_credit(
                    user=user,
                    amount=amount,
                    drop_id=drop_id,
                )
            )
            await session.execute(
                update(CoinDrop)
                .where(CoinDrop.id == drop_id)
                .values(credited=True, credited_at=now)
            )
        await session.commit()

    bot_username = settings.bot_username
    if bot_username:
        bot_hint = f"👉 Перейди в бота: @{html.escape(bot_username)} и нажми /start."
    else:
        bot_hint = "👉 Перейди в бота и нажми /start, чтобы забрать награду."

    text = (
        "💥 <b>Мешок поднят!</b>\n"
        f"Победитель: {winner_label}\n"
        f"Выигрыш: <b>{amount} GSNS Coins</b>\n\n"
        f"{bot_hint}"
    )

    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass

    await callback.answer("Мешок твой!")
