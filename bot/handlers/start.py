"""Module for start functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    ModerationChat,
    ModerationRestriction,
    Review,
    User,
    WalletTransaction,
)
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.common import deals_menu_kb, main_menu_kb, referral_kb
from bot.services.trust import apply_trust_event
from bot.services.weekly_rewards import grant_pending_rewards
from bot.utils.texts import TOOLS_TEXT, WELCOME_TEXT

router = Router()


def _format_until(value) -> str:
    """Handle format until.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    if not value:
        return "-"
    try:
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "-"


async def _sync_user_restrictions(
    bot,
    sessionmaker: async_sessionmaker,
    user_id: int,
) -> None:
    """Handle sync user restrictions.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        user_id: Value for user_id.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat).where(ModerationChat.active.is_(True))
        )
        chats = result.scalars().all()

    for chat in chats:
        # Pull live chat membership state to keep restrictions in sync.
        try:
            member = await bot.get_chat_member(chat.chat_id, user_id)
        except Exception:
            continue

        action = None
        until_date = None
        status = getattr(member, "status", None)
        if status == "kicked":
            action = "ban"
        elif status == "restricted":
            can_send = getattr(member, "can_send_messages", True)
            if can_send is False:
                action = "mute"
                until_date = getattr(member, "until_date", None)

        async with sessionmaker() as session:
            # Persist current restriction state and apply trust impact once.
            if action:
                result = await session.execute(
                    select(ModerationRestriction).where(
                        ModerationRestriction.chat_id == chat.chat_id,
                        ModerationRestriction.user_id == user_id,
                        ModerationRestriction.action == action,
                        ModerationRestriction.active.is_(True),
                    )
                )
                record = result.scalar_one_or_none()
                if record:
                    record.until_date = until_date
                    await session.commit()
                else:
                    record = ModerationRestriction(
                        chat_id=chat.chat_id,
                        user_id=user_id,
                        action=action,
                        reason="\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430",
                        until_date=until_date,
                        active=True,
                    )
                    session.add(record)
                    await session.commit()
                    if action == "ban":
                        await apply_trust_event(
                            session,
                            user_id,
                            "chat_ban",
                            -20,
                            record.reason
                            or "\u0411\u0430\u043d \u0432 \u0447\u0430\u0442\u0435",
                            ref_type="restriction",
                            ref_id=record.id,
                        )
                    elif action == "mute":
                        await apply_trust_event(
                            session,
                            user_id,
                            "chat_mute",
                            -5,
                            record.reason
                            or "\u041c\u0443\u0442 \u0432 \u0447\u0430\u0442\u0435",
                            ref_type="restriction",
                            ref_id=record.id,
                        )
            else:
                result = await session.execute(
                    select(ModerationRestriction).where(
                        ModerationRestriction.chat_id == chat.chat_id,
                        ModerationRestriction.user_id == user_id,
                        ModerationRestriction.active.is_(True),
                    )
                )
                for record in result.scalars().all():
                    record.active = False
                await session.commit()


async def _send_restrictions_summary(
    message: Message, sessionmaker: async_sessionmaker
) -> None:
    """Handle send restrictions summary.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationRestriction, ModerationChat)
            .join(
                ModerationChat,
                ModerationChat.chat_id == ModerationRestriction.chat_id,
                isouter=True,
            )
            .where(
                ModerationRestriction.user_id == message.from_user.id,
                ModerationRestriction.active.is_(True),
            )
        )
        rows = result.all()

    if not rows:
        return

    lines = [
        "\u26a0\ufe0f \u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u044f \u0432 \u043c\u043e\u0434\u0435\u0440\u0438\u0440\u0443\u0435\u043c\u044b\u0445 \u0447\u0430\u0442\u0430\u0445:"
    ]
    for record, chat in rows:
        chat_title = chat.title if chat and chat.title else str(record.chat_id)
        action_label = (
            "\u0411\u0430\u043d" if record.action == "ban" else "\u041c\u0443\u0442"
        )
        until_label = (
            f", \u0434\u043e {_format_until(record.until_date)}"
            if record.action == "mute"
            else ""
        )
        reason = (
            record.reason
            or "\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430"
        )
        lines.append(
            f"{action_label} | {chat_title}{until_label}\n\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason}"
        )
    await message.answer("\n\n".join(lines))


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle cmd start.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    await state.clear()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        created = False
        if not user:
            created = True
            referrer_id = None
            if message.text and len(message.text.split()) > 1:
                try:
                    referrer_id = int(message.text.split()[1])
                except ValueError:
                    referrer_id = None
            user = User(
                id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                referrer_id=referrer_id,
            )
            session.add(user)
            await session.commit()
        else:
            await get_or_create_user(session, message.from_user)

        if created and user.referrer_id:
            result = await session.execute(
                select(User).where(User.id == user.referrer_id)
            )
            referrer = result.scalar_one_or_none()
            if referrer:
                referrer.balance = (referrer.balance or 0) + settings.referral_bonus
                session.add(
                    WalletTransaction(
                        user_id=referrer.id,
                        amount=settings.referral_bonus,
                        type="referral",
                        description=f"Реферал {user.id}",
                    )
                )
                await session.commit()
        users_count = await session.execute(select(func.count(User.id)))
        reviews_count = await session.execute(
            select(func.count(Review.id))
            .select_from(Review)
            .join(User, User.id == Review.target_id)
            .where(User.role == "guarantor")
        )

    users_total = users_count.scalar_one() or 0
    reviews_total = reviews_count.scalar_one() or 0
    trust_block = (
        f"\n\nНам доверяют: {users_total} пользователей.\n"
        f"Отзывы о гарантах: {reviews_total} отзывов."
    )
    await message.answer(
        f"{WELCOME_TEXT}{trust_block}",
        reply_markup=main_menu_kb(),
    )
    await _sync_user_restrictions(message.bot, sessionmaker, message.from_user.id)
    await _send_restrictions_summary(message, sessionmaker)
    await message.answer(
        "🎁 Донат с выгодным курсом для вашей игры:",
        reply_markup=referral_kb(),
    )
    await grant_pending_rewards(message.bot, sessionmaker, message.from_user.id)


@router.message(F.text == "/id")
async def cmd_id(message: Message) -> None:
    """Handle cmd id.

    Args:
        message: Value for message.
    """
    thread_id = message.message_thread_id
    text = (
        f"CHAT_ID: {message.chat.id}\n"
        f"TOPIC_ID: {thread_id if thread_id is not None else 'нет'}"
    )
    await message.answer(text)


@router.message(F.text == "📦 Сделки и объявления")
async def menu_deals(message: Message) -> None:
    """Handle menu deals.

    Args:
        message: Value for message.
    """
    await message.answer("Выберите действие.", reply_markup=deals_menu_kb())


@router.message(F.text == "⬅️ Назад")
async def menu_back(message: Message) -> None:
    """Handle menu back.

    Args:
        message: Value for message.
    """
    await message.answer("Главное меню.", reply_markup=main_menu_kb())


@router.message(F.text == "🧰 Инструменты")
async def menu_tools(message: Message) -> None:
    """Handle menu tools.

    Args:
        message: Value for message.
    """
    await message.answer(TOOLS_TEXT)


@router.message(F.text == "/cancel")
@router.message(F.text == "Отмена")
async def cancel_flow(message: Message, state: FSMContext) -> None:
    """Handle cancel flow.

    Args:
        message: Value for message.
        state: Value for state.
    """
    await state.clear()
    await message.answer("✅ Действие отменено.", reply_markup=main_menu_kb())
