"""Module for staff panel functionality."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    Ad,
    BroadcastRequest,
    Complaint,
    Deal,
    DealRoom,
    Dispute,
    Game,
    ModerationChat,
    ModerationWord,
    Review,
    StaffTask,
    TrustEvent,
    TrustState,
    User,
    WalletTransaction,
)
from bot.handlers.helpers import get_or_create_user
from bot.handlers.deals import (
    _assign_deal_room,
    _notify_room_pool_low,
    _release_deal_room,
)
from bot.keyboards.ads import deal_after_take_kb
from bot.keyboards.common import OWNER_PANEL_BUTTON, STAFF_PANEL_BUTTON
from bot.keyboards.staff import (
    admin_panel_kb,
    complaint_kb,
    complaint_filter_kb,
    confirm_action_kb,
    designer_panel_kb,
    confirm_deal_action_kb,
    guarantor_panel_kb,
    guarantor_deal_kb,
    moderation_ad_kb,
    moderation_filter_kb,
    moderator_panel_kb,
    owner_panel_kb,
    review_dispute_kb,
    review_kb,
    staff_manage_kb,
    task_kb,
)
from bot.keyboards.chat_moderation import chat_moderation_kb
from bot.services.fees import calculate_fee
from bot.services.daily_report import send_daily_report
from bot.services.trust import (
    apply_trust_event,
    get_trust_score,
    rollback_trust_event,
    set_trust_frozen,
)
from bot.utils.broadcasts import create_broadcast_request
from bot.utils.vip import free_fee_active, is_vip_until
from bot.utils.admin_target import (
    clear_admin_target,
    get_admin_target,
    set_admin_target,
)
from bot.utils.roles import is_owner, is_staff, role_label

router = Router()


async def _send_broadcast_message(bot, user_id: int, text: str) -> bool:
    """Handle send broadcast message."""
    try:
        await bot.send_message(user_id, text)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception:
        return False


class OwnerStates(StatesGroup):
    """Represent OwnerStates.

    Attributes:
        set_role: Attribute value.
        task_title: Attribute value.
        task_desc: Attribute value.
        review_edit: Attribute value.
        admin_deal: Attribute value.
    """

    set_role = State()
    task_title = State()
    task_desc = State()
    review_edit = State()
    admin_deal = State()


class AdRejectStates(StatesGroup):
    """Represent AdRejectStates.

    Attributes:
        ad_id: Attribute value.
        reason: Attribute value.
    """

    ad_id = State()
    reason = State()


class DisputeStates(StatesGroup):
    """Represent DisputeStates.

    Attributes:
        deal_id: Attribute value.
        reason: Attribute value.
    """

    deal_id = State()
    reason = State()


class ChatModerationStates(StatesGroup):
    """Represent ChatModerationStates.

    Attributes:
        add_chat: Attribute value.
    """

    add_chat = State()


class ModerationWordStates(StatesGroup):
    """Represent ModerationWordStates.

    Attributes:
        add_word: Attribute value.
        remove_word: Attribute value.
    """

    add_word = State()
    remove_word = State()


class TrustStates(StatesGroup):
    """Represent TrustStates.

    Attributes:
        user_id: Attribute value.
    """

    user_id = State()


class TrustByUserStates(StatesGroup):
    """Represent TrustByUserStates.

    Attributes:
        user_id: Attribute value.
    """

    user_id = State()


def _is_admin(role: str) -> bool:
    """Handle is admin.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    return role in {"owner", "admin"}


def _is_moderator(role: str) -> bool:
    """Handle is moderator.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    return role in {"owner", "admin", "moderator"}


def _is_guarantor(role: str) -> bool:
    """Handle is guarantor.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    return role in {"owner", "admin", "guarantor"}


@router.message(F.text.startswith("/set_admin_topic"))
async def set_admin_topic(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Set admin topic.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_admin(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await message.answer("Нет доступа.")
            return

    parts = message.text.split()
    if len(parts) > 1:
        try:
            topic_id = int(parts[1])
        except ValueError:
            await message.answer("Формат: /set_admin_topic TOPIC_ID")
            return
        set_admin_target(message.chat.id, topic_id)
        await message.answer(
            f"Админ-ветка установлена: CHAT_ID={message.chat.id}, TOPIC_ID={topic_id}"
        )
        return

    if message.message_thread_id is None:
        await message.answer(
            "Команду нужно отправить внутри темы или передать TOPIC_ID."
        )
        return

    set_admin_target(message.chat.id, message.message_thread_id)
    await message.answer(
        f"Админ-ветка установлена: CHAT_ID={message.chat.id}, TOPIC_ID={message.message_thread_id}"
    )


@router.message(F.text == "/admin_target")
async def admin_target(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle admin target.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not is_owner(user.role, settings.owner_ids, user.id):
            return
    chat_id, topic_id = get_admin_target(settings)
    await message.answer(
        f"Текущая админ-ветка: CHAT_ID={chat_id}, TOPIC_ID={topic_id or 'нет'}"
    )


@router.message(F.text == "/admin_report_target")
async def admin_report_target(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle admin report target.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_admin(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            return
    topic_id = settings.admin_topic_id if settings.admin_topic_id else "нет"
    await message.answer(
        f"ADMIN_CHAT_ID={settings.admin_chat_id}, ADMIN_TOPIC_ID={topic_id}"
    )


@router.message(F.text == "/clear_admin_topic")
async def clear_admin_topic(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle clear admin topic.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_admin(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            return
    clear_admin_target()
    await message.answer("Админ-ветка сброшена на общий чат.")


@router.message(F.text == "/ping_admin")
async def ping_admin(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle ping admin.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_admin(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            return
    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        await message.answer("ADMIN_CHAT_ID не задан.")
        return
    await message.bot.send_message(
        chat_id,
        "Тест админ‑канала: сообщение доставлено.",
        message_thread_id=topic_id,
    )
    await message.answer("Тест отправлен.")


@router.message(F.text == "/daily_report")
async def daily_report_now(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle daily report now.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_admin(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            return
    await send_daily_report(message.bot, sessionmaker, settings)
    await message.answer("????? ?????????.")


async def _load_user(sessionmaker, tg_user) -> User:
    """Handle load user.

    Args:
        sessionmaker: Value for sessionmaker.
        tg_user: Value for tg_user.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        return await get_or_create_user(session, tg_user)


async def _resolve_user_id(session, token: str) -> int | None:
    """Handle resolve user id.

    Args:
        session: Value for session.
        token: Value for token.

    Returns:
        Return value.
    """
    token = token.strip()
    if token.startswith("@"):
        username = token[1:].lower()
        result = await session.execute(
            select(User).where(func.lower(User.username) == username)
        )
        user = result.scalar_one_or_none()
        return user.id if user else None
    try:
        user_id = int(token)
    except ValueError:
        return None
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return user.id if user else None


async def _recalc_rating(session, user_id: int) -> None:
    """Handle recalc rating.

    Args:
        session: Value for session.
        user_id: Value for user_id.
    """
    result = await session.execute(
        select(func.count(Review.id), func.avg(Review.rating)).where(
            Review.target_id == user_id, Review.status == "active"
        )
    )
    count, avg = result.one()
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.rating_count = count or 0
        user.rating_avg = float(avg) if avg is not None else None
        await session.commit()


@router.message(F.text.in_({OWNER_PANEL_BUTTON, STAFF_PANEL_BUTTON}))
async def staff_entry(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle staff entry.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if user.id in settings.owner_ids and user.role != "owner":
            user.role = "owner"
            await session.commit()

    if is_owner(user.role, settings.owner_ids, user.id):
        await message.answer(
            "Панель владельца GSNS.\n"
            "Основные обязанности:\n"
            "— стратегия и финконтроль\n"
            "— назначение ролей и доступов\n"
            "— контроль модерации/отзывов/споров\n"
            "— ключевые настройки и витрина услуг",
            reply_markup=owner_panel_kb(),
        )
        return

    if not is_staff(user.role):
        await message.answer("Нет доступа.")
        return

    if user.role == "guarantor":
        await message.answer(
            "Рабочая панель гаранта.\n"
            "Основные обязанности:\n"
            "— брать сделки на смене\n"
            "— вести спорные ситуации\n"
            "— проверять пользователей\n"
            "— фиксировать результаты",
            reply_markup=guarantor_panel_kb(user.on_shift),
        )
    elif user.role == "moderator":
        await message.answer(
            "Панель модератора.\n"
            "Основные обязанности:\n"
            "— модерация объявлений\n"
            "— обработка жалоб\n"
            "— поддержка тикетов\n"
            "— база скамеров и проверки",
            reply_markup=moderator_panel_kb(),
        )
    elif user.role == "designer":
        await message.answer(
            "Панель дизайнера.\n"
            "Основные обязанности:\n"
            "— выполнение задач от владельца\n"
            "— оформление материалов и кнопок\n"
            "— подготовка визуалов",
            reply_markup=designer_panel_kb(),
        )
    elif user.role == "admin":
        await message.answer(
            "Панель администратора.\n"
            "Основные обязанности:\n"
            "— управление услугами и VIP\n"
            "— обработка пополнений\n"
            "— контроль модерации и жалоб\n"
            "— запуск рассылок после модерации",
            reply_markup=admin_panel_kb(),
        )
    else:
        await message.answer("Панель персонала.", reply_markup=owner_panel_kb())


@router.callback_query(F.data == "owner:staff")
async def owner_staff(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner staff.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    await callback.message.answer(
        "Управление персоналом:", reply_markup=staff_manage_kb()
    )
    await callback.answer()


async def _load_moderation_chats(
    sessionmaker: async_sessionmaker,
) -> list[ModerationChat]:
    """Handle load moderation chats.

    Args:
        sessionmaker: Value for sessionmaker.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat).order_by(ModerationChat.id.asc())
        )
        return result.scalars().all()


def _moderation_chats_text(chats: list[ModerationChat]) -> str:
    """Handle moderation chats text.

    Args:
        chats: Value for chats.

    Returns:
        Return value.
    """
    if not chats:
        return "Чаты для модерации пока не добавлены."
    lines = ["🛡️ <b>Чаты на модерации</b>:"]
    for chat in chats:
        status = "🟢" if chat.active else "🔴"
        title = chat.title or f"ID {chat.chat_id}"
        lines.append(f"{status} {title} ({chat.chat_id})")
    return "\n".join(lines)


def _mod_blacklist_kb() -> InlineKeyboardMarkup:
    """Handle mod blacklist kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить слово",
                    callback_data="mod_blacklist:add",
                ),
                InlineKeyboardButton(
                    text="➖ Удалить слово",
                    callback_data="mod_blacklist:remove",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data="mod_blacklist:menu",
                )
            ],
        ]
    )


@router.callback_query(F.data == "owner:chat_moderation")
async def owner_chat_moderation(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner chat moderation.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    chats = await _load_moderation_chats(sessionmaker)
    kb = chat_moderation_kb([(c.chat_id, c.title, c.active) for c in chats])
    await callback.message.answer(_moderation_chats_text(chats), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "chat_mod_add")
async def chat_mod_add(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle chat mod add.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    await state.set_state(ChatModerationStates.add_chat)
    await callback.message.answer(
        "Перешлите сообщение из чата, который нужно поставить на модерацию.\n"
        "Для отмены — /cancel."
    )
    await callback.answer()


@router.message(ChatModerationStates.add_chat)
async def chat_mod_add_message(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle chat mod add message.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if message.text and message.text.strip() in {"/cancel", "Отмена"}:
        await state.clear()
        await message.answer("❌ Действие отменено.")
        return

    chat = message.forward_from_chat
    if not chat and message.chat.type in {"group", "supergroup"}:
        chat = message.chat

    if not chat:
        await message.answer("Не вижу чат. Перешлите сообщение из нужной группы.")
        return

    async with sessionmaker() as session:
        owner = await get_or_create_user(session, message.from_user)
        result = await session.execute(
            select(ModerationChat).where(ModerationChat.chat_id == chat.id)
        )
        record = result.scalar_one_or_none()
        if record:
            record.active = True
            record.title = chat.title
        else:
            record = ModerationChat(
                chat_id=chat.id,
                title=chat.title,
                active=True,
                added_by=owner.id,
            )
            session.add(record)
        await session.commit()

    await state.clear()
    await message.answer(f"✅ Чат добавлен в модерацию: {chat.title or chat.id}")


@router.callback_query(F.data.startswith("chat_mod_toggle:"))
async def chat_mod_toggle(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle chat mod toggle.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return

    chat_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat).where(ModerationChat.chat_id == chat_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            await callback.answer("Чат не найден.")
            return
        record.active = not record.active
        await session.commit()
        status = "включена" if record.active else "выключена"
    await callback.answer(f"Модерация {status}.")


async def _load_blacklist_words(
    sessionmaker: async_sessionmaker,
) -> list[str]:
    """Handle load blacklist words.

    Args:
        sessionmaker: Value for sessionmaker.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationWord.word).where(ModerationWord.active.is_(True))
        )
        return [row[0] for row in result.all() if row[0]]


@router.callback_query(F.data == "mod_blacklist:menu")
async def mod_blacklist_menu(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod blacklist menu.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if user.role not in {"owner", "admin", "moderator"} and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await callback.answer("Нет доступа.")
        return

    custom_words = await _load_blacklist_words(sessionmaker)
    system_words = [w for w in settings.moderation_blacklist if w.strip()]
    text = "🚫 <b>ЧС модерации</b>\n\n" "<b>Системный список</b>:\n" + (
        "\n".join(f"• {w}" for w in system_words) if system_words else "• пусто"
    ) + "\n\n<b>Пользовательский список</b>:\n" + (
        "\n".join(f"• {w}" for w in custom_words) if custom_words else "• пусто"
    )
    await callback.message.answer(text, reply_markup=_mod_blacklist_kb())
    await callback.answer()


@router.callback_query(F.data == "mod_blacklist:add")
async def mod_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod blacklist add.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if user.role not in {"owner", "admin", "moderator"} and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await callback.answer("Нет доступа.")
        return
    await state.set_state(ModerationWordStates.add_word)
    await callback.message.answer("Введите слово/фразу для добавления в ЧС.")
    await callback.answer()


@router.callback_query(F.data == "mod_blacklist:remove")
async def mod_blacklist_remove(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod blacklist remove.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if user.role not in {"owner", "admin", "moderator"} and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await callback.answer("Нет доступа.")
        return
    await state.set_state(ModerationWordStates.remove_word)
    await callback.message.answer("Введите слово/фразу для удаления из ЧС.")
    await callback.answer()


@router.message(ModerationWordStates.add_word)
async def mod_blacklist_add_word(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod blacklist add word.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, message.from_user)
    if user.role not in {"owner", "admin", "moderator"} and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await state.clear()
        return
    if message.text and message.text.strip() in {"/cancel", "Отмена"}:
        await state.clear()
        await message.answer("❌ Действие отменено.")
        return
    word = (message.text or "").strip().lower()
    if not word:
        await message.answer("Введите слово/фразу.")
        return
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationWord).where(ModerationWord.word == word)
        )
        record = result.scalar_one_or_none()
        if record:
            record.active = True
        else:
            session.add(ModerationWord(word=word, active=True))
        await session.commit()
    await state.clear()
    await message.answer("✅ Слово добавлено в ЧС.")


@router.message(ModerationWordStates.remove_word)
async def mod_blacklist_remove_word(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod blacklist remove word.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, message.from_user)
    if user.role not in {"owner", "admin", "moderator"} and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await state.clear()
        return
    if message.text and message.text.strip() in {"/cancel", "Отмена"}:
        await state.clear()
        await message.answer("❌ Действие отменено.")
        return
    word = (message.text or "").strip().lower()
    if not word:
        await message.answer("Введите слово/фразу.")
        return
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationWord).where(ModerationWord.word == word)
        )
        record = result.scalar_one_or_none()
        if record:
            record.active = False
            await session.commit()
            await message.answer("✅ Слово удалено из ЧС.")
        else:
            await message.answer("Слова нет в пользовательском списке.")
    await state.clear()


@router.callback_query(F.data == "owner:roles")
async def owner_roles(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner roles.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    await callback.message.answer(
        "Доступные роли: owner, admin, moderator, designer, guarantor, user."
    )
    await callback.answer()


@router.callback_query(F.data == "owner:set_role")
async def owner_set_role(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner set role.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_admin(user.role) and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await callback.answer("Нет доступа.")
        return
    await state.set_state(OwnerStates.set_role)
    await callback.message.answer("Введите: user_id role")
    await callback.answer()


@router.message(OwnerStates.set_role)
async def owner_set_role_value(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner set role value.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        owner = await get_or_create_user(session, message.from_user)
        if not _is_admin(owner.role) and not is_owner(
            owner.role, settings.owner_ids, owner.id
        ):
            await message.answer("Нет прав.")
            await state.clear()
            return

        target_user = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
        elif message.forward_from:
            target_user = message.forward_from

        parts = message.text.split() if message.text else []
        role = None
        user_id = None

        if target_user:
            if len(parts) < 1:
                await message.answer("Формат: role (в ответе/пересылке)")
                return
            user_id = target_user.id
            role = parts[0].strip().lower()
        else:
            if len(parts) < 2:
                await message.answer("Формат: user_id role или @username role")
                return
            target = parts[0].strip()
            role = parts[1].strip().lower()
            if target.startswith("@"):
                username = target[1:]
                result = await session.execute(
                    select(User).where(User.username == username)
                )
                user = result.scalar_one_or_none()
                if not user:
                    await message.answer("Пользователь не найден. Пусть нажмет /start.")
                    return
                user_id = user.id
            else:
                user_id = int(target)

        if role not in {"admin", "moderator", "designer", "guarantor"}:
            await message.answer("Неизвестная роль.")
            return

        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(id=user_id, role=role)
            session.add(user)
        else:
            user.role = role
        await session.commit()

    await state.clear()
    await message.answer(f"Роль обновлена: {user_id} -> {role}")
    await _log_admin(
        message.bot,
        settings,
        f"Роль обновлена: {user_id} -> {role} (кто: {owner.id})",
    )


@router.message(F.text.startswith("/fire"))
async def fire_staff(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Remove staff role from a user."""
    async with sessionmaker() as session:
        owner = await get_or_create_user(session, message.from_user)
        if not is_owner(owner.role, settings.owner_ids, owner.id):
            return

        target_user = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
        elif message.forward_from:
            target_user = message.forward_from

        parts = message.text.split() if message.text else []
        user_id = None

        if target_user:
            user_id = target_user.id
        else:
            if len(parts) < 2:
                await message.answer("Usage: /fire user_id or reply")
                return
            target = parts[1].strip()
            if target.startswith("@"):
                username = target[1:]
                result = await session.execute(
                    select(User).where(User.username == username)
                )
                user = result.scalar_one_or_none()
                if not user:
                    await message.answer("User not found. Ask them to /start.")
                    return
                user_id = user.id
            else:
                if not target.isdigit():
                    await message.answer("Usage: /fire user_id or reply")
                    return
                user_id = int(target)

        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("User not found. Ask them to /start.")
            return
        if is_owner(user.role, settings.owner_ids, user.id):
            await message.answer("Cannot remove owner.")
            return

        user.role = "user"
        user.on_shift = False
        await session.commit()

    await message.answer(f"Staff removed: {user_id}")
    await _log_admin(
        message.bot,
        settings,
        f"Staff removed: {user_id} (by {owner.id})",
    )


@router.callback_query(F.data == "owner:list_staff")
async def owner_list_staff(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner list staff.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(
                User.role.in_({"owner", "admin", "moderator", "designer", "guarantor"})
            )
        )
        users = result.scalars().all()

    if not users:
        await callback.message.answer("Персонал не найден.")
        await callback.answer()
        return

    lines = ["Персонал:"]
    for user in users:
        lines.append(f"- {user.id} {role_label(user.role)}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "owner:moderation")
async def owner_moderation(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner moderation.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    await callback.message.answer(
        "Фильтр модерации:", reply_markup=moderation_filter_kb()
    )
    await _show_ads_by_status(callback, sessionmaker, "pending")


@router.callback_query(F.data == "moderator:ads")
async def moderator_ads(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle moderator ads.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    await callback.message.answer(
        "Фильтр модерации:", reply_markup=moderation_filter_kb()
    )
    await _show_ads_by_status(callback, sessionmaker, "pending")


@router.callback_query(F.data.startswith("mod_filter:"))
async def mod_filter(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    """Handle mod filter.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    status = callback.data.split(":")[1]
    await _show_ads_by_status(callback, sessionmaker, status)
    await callback.answer()


async def _show_ads_by_status(
    callback: CallbackQuery, sessionmaker: async_sessionmaker, status: str
) -> None:
    """Handle show ads by status.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        status: Value for status.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Ad)
            .where(Ad.moderation_status == status)
            .order_by(Ad.id.desc())
            .limit(20)
        )
        ads = result.scalars().all()

    if not ads:
        await callback.message.answer("Нет объявлений по выбранному фильтру.")
        await callback.answer()
        return

    for ad in ads:
        text = (
            f"{ad.title}\n"
            f"Цена: {ad.price} ₽\n"
            f"ID: {ad.id}\n"
            f"Продавец: {ad.seller_id}\n"
            f"Статус: {ad.moderation_status}"
        )
        await callback.message.answer(text, reply_markup=moderation_ad_kb(ad.id))


@router.callback_query(F.data.startswith("mod_approve:"))
async def mod_approve(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod approve.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    ad_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad:
            await callback.answer("Объявление не найдено.")
            return
        ad.moderation_status = "approved"
        ad.moderation_reason = None
        await session.commit()
        result = await session.execute(select(User).where(User.id == ad.seller_id))
        seller = result.scalar_one_or_none()
        if seller and is_vip_until(seller.vip_until) and ad.account_id:
            game_name = "-"
            if ad.game_id:
                game_result = await session.execute(
                    select(Game.name).where(Game.id == ad.game_id)
                )
                game_row = game_result.scalar_one_or_none()
                if game_row:
                    game_name = game_row

            seller_label = f"@{seller.username}" if seller.username else "Продавец без ника"
            price_label = (
                f"{ad.price:.2f} ₽" if ad.price is not None else "Договорная"
            )
            description = (ad.description or "").strip()
            text = (
                "💎 VIP-объявление GSNS 💎\n"
                f"🎮 Игра: {game_name}\n"
                f"🔖 Название: {ad.title}\n"
                f"💰 Цена: {price_label}\n"
                f"👤 Продавец: {seller_label}\n\n"
                f"✳️ ID объявления: {ad.id}\n"
            )
            if description:
                text += f"\n📜 Описание:\n{description}"
            await create_broadcast_request(
                session,
                callback.bot,
                settings,
                creator_id=seller.id,
                text=text,
                kind="vip_auto",
                cost=0,
                ad_id=ad.id,
            )
    await callback.message.answer("Объявление одобрено.")
    await _log_admin(
        callback.bot,
        settings,
        f"Модерация: одобрено объявление #{ad_id} (модератор {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mod_reject:"))
async def mod_reject(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod reject."""

    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    ad_id = int(callback.data.split(":")[1])
    await state.set_state(AdRejectStates.reason)
    await state.update_data(ad_id=ad_id)
    await callback.message.answer("Укажите причину отклонения объявления.")
    await callback.answer()


@router.message(AdRejectStates.reason)
async def mod_reject_reason(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle mod reject reason."""

    user = await _load_user(sessionmaker, message.from_user)
    if not _is_moderator(user.role):
        await state.clear()
        return
    if message.text and message.text.strip().lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer("Действие отменено.")
        return
    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Укажите причину отклонения.")
        return
    data = await state.get_data()
    ad_id = data.get("ad_id")
    if not ad_id:
        await state.clear()
        await message.answer("Не найдено объявление для отклонения.")
        return
    seller_id = None
    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad:
            await state.clear()
            await message.answer("Объявление не найдено.")
            return
        ad.moderation_status = "rejected"
        ad.moderation_reason = reason
        ad.active = False
        seller_id = ad.seller_id
        await session.commit()
    if seller_id:
        try:
            await message.bot.send_message(
                seller_id,
                f"Ваше объявление #{ad_id} отклонено. Причина: {reason}",
            )
        except Exception:
            pass
    await message.answer("Объявление отклонено.")
    await _log_admin(
        message.bot,
        settings,
        f"Модерация: отклонено объявление #{ad_id} (модератор {message.from_user.id}) Причина: {reason}",
    )
    await state.clear()


@router.callback_query(F.data == "moderator:complaints")
async def moderator_complaints(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle moderator complaints.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    await callback.message.answer("Фильтр жалоб:", reply_markup=complaint_filter_kb())
    await _show_complaints(callback, sessionmaker, "open")


@router.callback_query(F.data.startswith("complaint_filter:"))
async def complaint_filter(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle complaint filter.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    status = callback.data.split(":")[1]
    await _show_complaints(callback, sessionmaker, status)
    await callback.answer()


async def _show_complaints(
    callback: CallbackQuery, sessionmaker: async_sessionmaker, status: str
) -> None:
    """Handle show complaints.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        status: Value for status.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Complaint)
            .where(Complaint.status == status)
            .order_by(Complaint.id.desc())
            .limit(20)
        )
        complaints = result.scalars().all()

    if not complaints:
        await callback.message.answer("Жалоб по выбранному фильтру нет.")
        return

    for complaint in complaints:
        text = (
            f"Жалоба #{complaint.id}\n"
            f"Объявление: {complaint.ad_id}\n"
            f"Автор: {complaint.reporter_id}\n"
            f"Статус: {complaint.status}\n"
            f"Причина: {complaint.reason}"
        )
        await callback.message.answer(text, reply_markup=complaint_kb(complaint.id))


@router.callback_query(F.data.startswith("complaint_close:"))
async def complaint_close(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle complaint close.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    complaint_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(Complaint).where(Complaint.id == complaint_id)
        )
        complaint = result.scalar_one_or_none()
        if not complaint:
            await callback.answer("Жалоба не найдена.")
            return
        complaint.status = "closed"
        await session.commit()
    await callback.message.answer("Жалоба закрыта.")
    await _log_admin(
        callback.bot,
        settings,
        f"Жалоба закрыта #{complaint_id} (модератор {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("complaint_delete_req:"))
async def complaint_delete_req(callback: CallbackQuery) -> None:
    """Handle complaint delete req.

    Args:
        callback: Value for callback.
    """
    complaint_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"Удалить жалобу #{complaint_id}?",
        reply_markup=confirm_action_kb("complaint_delete", complaint_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("complaint_delete_yes:"))
async def complaint_delete_yes(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle complaint delete yes.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    complaint_id = int(callback.data.split(":")[1])
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    async with sessionmaker() as session:
        result = await session.execute(
            select(Complaint).where(Complaint.id == complaint_id)
        )
        complaint = result.scalar_one_or_none()
        if not complaint:
            await callback.answer("Жалоба не найдена.")
            return
        await session.delete(complaint)
        await session.commit()
    await callback.message.answer("Жалоба удалена.")
    await _log_admin(
        callback.bot,
        settings,
        f"Жалоба удалена #{complaint_id} (модератор {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("complaint_delete_no:"))
async def complaint_delete_no(callback: CallbackQuery) -> None:
    """Handle complaint delete no.

    Args:
        callback: Value for callback.
    """
    await callback.message.answer("Действие отменено.")
    await callback.answer()


@router.callback_query(F.data == "guarantor:shift")
async def guarantor_shift(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle guarantor shift.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()
        if not user:
            await callback.answer("Профиль не найден.")
            return
        user.on_shift = not user.on_shift
        await session.commit()

    await callback.message.answer(
        "Смена обновлена.",
        reply_markup=guarantor_panel_kb(user.on_shift),
    )
    await callback.answer()
    await callback.answer()


@router.callback_query(F.data == "guarantor:deals")
async def guarantor_deals(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle guarantor deals.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        seller = aliased(User)
        buyer = aliased(User)
        result = await session.execute(
            select(Deal, Ad, Game, seller, buyer)
            .join(Ad, Ad.id == Deal.ad_id, isouter=True)
            .join(Game, Game.id == Ad.game_id, isouter=True)
            .join(seller, seller.id == Deal.seller_id)
            .join(buyer, buyer.id == Deal.buyer_id)
            .where(Deal.guarantee_id == callback.from_user.id)
            .order_by(Deal.id.desc())
            .limit(20)
        )
        rows = result.all()

    if not rows:
        await callback.message.answer("Сделок нет.")
        await callback.answer()
        return

    for deal, ad, game, seller, buyer in rows:
        game_name = game.name if game else "-"
        ad_title = ad.title if ad else "-"
        description = ad.description if ad else "-"
        seller_label = (
            f"{seller.id} (@{seller.username})" if seller.username else str(seller.id)
        )
        buyer_label = (
            f"{buyer.id} (@{buyer.username})" if buyer.username else str(buyer.id)
        )
        text = (
            f"Сделка #{deal.id}\n"
            f"Статус: {deal.status}\n"
            f"Тип: {deal.deal_type}\n"
            f"Игра: {game_name}\n"
            f"Лот: {ad_title}\n"
            f"Описание: {description}\n"
            f"Цена: {deal.price or '-'} ₽\n"
            f"Продавец: {seller_label}\n"
            f"Покупатель: {buyer_label}"
        )
        await callback.message.answer(text, reply_markup=guarantor_deal_kb(deal.id))
    await callback.answer()


@router.message(F.text.startswith("/deal_panel"))
async def guarantor_deal_panel(
    message: Message, sessionmaker: async_sessionmaker
) -> None:
    """Handle guarantor deal panel.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Использование: /deal_panel DEAL_ID")
        return
    deal_id = int(parts[1].strip())

    async with sessionmaker() as session:
        guarantor = await get_or_create_user(session, message.from_user)
        seller = aliased(User)
        buyer = aliased(User)
        result = await session.execute(
            select(Deal, Ad, Game, seller, buyer)
            .join(Ad, Ad.id == Deal.ad_id, isouter=True)
            .join(Game, Game.id == Ad.game_id, isouter=True)
            .join(seller, seller.id == Deal.seller_id)
            .join(buyer, buyer.id == Deal.buyer_id)
            .where(Deal.id == deal_id)
        )
        row = result.first()

    if not row:
        await message.answer("Сделка не найдена.")
        return

    deal, ad, game, seller, buyer = row
    if deal.guarantee_id != guarantor.id:
        await message.answer("Нет доступа к этой сделке.")
        return

    game_name = game.name if game else "-"
    ad_title = ad.title if ad else "-"
    description = ad.description if ad else "-"
    seller_label = (
        f"{seller.id} (@{seller.username})" if seller.username else str(seller.id)
    )
    buyer_label = f"{buyer.id} (@{buyer.username})" if buyer.username else str(buyer.id)
    text = (
        f"Сделка #{deal.id}\n"
        f"Статус: {deal.status}\n"
        f"Тип: {deal.deal_type}\n"
        f"Игра: {game_name}\n"
        f"Лот: {ad_title}\n"
        f"Описание: {description}\n"
        f"Цена: {deal.price or '-'} ₽\n"
        f"Продавец: {seller_label}\n"
        f"Покупатель: {buyer_label}"
    )
    await message.answer(text, reply_markup=guarantor_deal_kb(deal.id))


@router.callback_query(F.data == "guarantor:reviews")
async def guarantor_reviews(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle guarantor reviews.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Review)
            .where(Review.target_id == callback.from_user.id)
            .order_by(Review.id.desc())
            .limit(20)
        )
        reviews = result.scalars().all()

    if not reviews:
        await callback.message.answer("Отзывов нет.")
        await callback.answer()
        return

    for review in reviews:
        text = (
            f"Отзыв #{review.id}\n"
            f"Оценка: {review.rating}\n"
            f"Комментарий: {review.comment or '-'}\n"
            f"Статус: {review.status}"
        )
        await callback.message.answer(text, reply_markup=review_dispute_kb(review.id))
    await callback.answer()


@router.callback_query(F.data.startswith("review_dispute:"))
async def review_dispute(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle review dispute.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    review_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Review).where(Review.id == review_id))
        review = result.scalar_one_or_none()
        if not review or review.target_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        review.status = "disputed"
        await session.commit()

    chat_id, topic_id = get_admin_target(settings)
    if chat_id != 0:
        await callback.bot.send_message(
            chat_id,
            (
                f"Оспорен отзыв #{review_id}\n"
                f"Гарант: {callback.from_user.id}\n"
                f"Оценка: {review.rating}\n"
                f"Комментарий: {review.comment or '-'}"
            ),
            message_thread_id=topic_id,
        )

    await callback.message.answer("Отзыв отправлен на рассмотрение.")
    await callback.answer()


@router.callback_query(F.data == "guarantor:dispute")
async def guarantor_dispute(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle guarantor dispute.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.set_state(DisputeStates.deal_id)
    await callback.message.answer("Введите ID сделки для спора.")
    await callback.answer()


@router.message(DisputeStates.deal_id)
async def dispute_pick_deal(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle dispute pick deal.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    try:
        deal_id = int(message.text.strip())
    except ValueError:
        await message.answer("Неверный ID.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or deal.guarantee_id != message.from_user.id:
            await message.answer("Нет доступа к сделке.")
            return

    await state.update_data(deal_id=deal_id)
    await state.set_state(DisputeStates.reason)
    await message.answer("Опишите причину спора.")


@router.message(DisputeStates.reason)
async def dispute_reason(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle dispute reason.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    data = await state.get_data()
    deal_id = data.get("deal_id")
    if not deal_id:
        await state.clear()
        await message.answer("Сеанс истек.")
        return

    async with sessionmaker() as session:
        dispute = Dispute(
            deal_id=deal_id,
            reporter_id=message.from_user.id,
            description=message.text.strip(),
        )
        session.add(dispute)
        await session.commit()

    chat_id, topic_id = get_admin_target(settings)
    if chat_id != 0:
        await message.bot.send_message(
            chat_id,
            (
                f"Спор #{dispute.id} по сделке #{deal_id}\n"
                f"Гарант: {message.from_user.id}\n"
                f"Причина: {dispute.description}"
            ),
            message_thread_id=topic_id,
        )

    await state.clear()
    await message.answer("Спор создан и отправлен в админ-чат.")


@router.callback_query(F.data == "owner:disputes")
async def owner_disputes(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner disputes.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_admin(user.role) and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await callback.answer("Нет доступа.")
        return
    async with sessionmaker() as session:
        result = await session.execute(
            select(Dispute)
            .where(Dispute.status == "open")
            .order_by(Dispute.id.desc())
            .limit(20)
        )
        disputes = result.scalars().all()

    if not disputes:
        await callback.message.answer("Открытых споров нет.")
        await callback.answer()
        return

    for dispute in disputes:
        text = (
            f"Спор #{dispute.id}\n"
            f"Сделка: {dispute.deal_id}\n"
            f"Описание: {dispute.description}"
        )
        await callback.message.answer(
            text,
            reply_markup=confirm_action_kb("admin_deal", dispute.deal_id),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:reviews")
async def owner_reviews(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner reviews.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_admin(user.role):
        await callback.answer("Нет доступа.")
        return
    async with sessionmaker() as session:
        result = await session.execute(
            select(Review).order_by(Review.id.desc()).limit(20)
        )
        reviews = result.scalars().all()

    if not reviews:
        await callback.message.answer("Отзывов нет.")
        await callback.answer()
        return

    for review in reviews:
        text = (
            f"Отзыв #{review.id}\n"
            f"Оценка: {review.rating}\n"
            f"Комментарий: {review.comment or '-'}\n"
            f"Статус: {review.status}"
        )
        await callback.message.answer(text, reply_markup=review_kb(review.id))
    await callback.answer()


@router.callback_query(F.data.startswith("review_hide:"))
async def review_hide(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle review hide.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_admin(user.role):
        await callback.answer("Нет доступа.")
        return
    review_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Review).where(Review.id == review_id))
        review = result.scalar_one_or_none()
        if not review:
            await callback.answer("Отзыв не найден.")
            return
        review.status = "hidden"
        await session.commit()
        await _recalc_rating(session, review.target_id)
    await callback.message.answer("Отзыв скрыт.")
    await _log_admin(
        callback.bot,
        settings,
        f"Отзыв скрыт #{review_id} (кто: {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("review_edit:"))
async def review_edit(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle review edit.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_admin(user.role):
        await callback.answer("Нет доступа.")
        return
    review_id = int(callback.data.split(":")[1])
    await state.update_data(review_id=review_id)
    await state.set_state(OwnerStates.review_edit)
    await callback.message.answer("Введите: рейтинг(1-5) комментарий")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_deal_yes:"))
async def admin_deal_view(
    callback: CallbackQuery, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle admin deal view.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    deal_id = int(callback.data.split(":")[1])
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_admin(user.role) and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await callback.answer("Нет доступа.")
        return
    await _send_admin_deal_card(callback, sessionmaker, deal_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_deal_no:"))
async def admin_deal_no(callback: CallbackQuery) -> None:
    """Handle admin deal no.

    Args:
        callback: Value for callback.
    """
    await callback.message.answer("Просмотр отменен.")
    await callback.answer()


@router.message(OwnerStates.review_edit)
async def review_edit_value(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle review edit value.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    data = await state.get_data()
    review_id = data.get("review_id")
    if not review_id:
        await state.clear()
        await message.answer("Сеанс истек.")
        return

    parts = message.text.strip().split(maxsplit=1)
    try:
        rating = int(parts[0])
    except (ValueError, IndexError):
        await message.answer("Неверный формат.")
        return

    if rating < 1 or rating > 5:
        await message.answer("Рейтинг 1-5.")
        return

    comment = parts[1] if len(parts) > 1 else ""

    async with sessionmaker() as session:
        result = await session.execute(select(Review).where(Review.id == review_id))
        review = result.scalar_one_or_none()
        if not review:
            await message.answer("Отзыв не найден.")
            await state.clear()
            return
        review.rating = rating
        review.comment = comment
        review.status = "active"
        await session.commit()
        await _recalc_rating(session, review.target_id)

    await state.clear()
    await message.answer("Отзыв обновлен.")
    await _log_admin(
        message.bot,
        settings,
        f"Отзыв изменен #{review_id} (кто: {message.from_user.id})",
    )


async def _send_admin_deal_card(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    deal_id: int,
) -> None:
    """Handle send admin deal card.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        deal_id: Value for deal_id.
    """
    async with sessionmaker() as session:
        seller = aliased(User)
        buyer = aliased(User)
        guarantor = aliased(User)
        result = await session.execute(
            select(Deal, Ad, Game, seller, buyer, guarantor)
            .join(Ad, Ad.id == Deal.ad_id, isouter=True)
            .join(Game, Game.id == Ad.game_id, isouter=True)
            .join(seller, seller.id == Deal.seller_id)
            .join(buyer, buyer.id == Deal.buyer_id)
            .join(guarantor, guarantor.id == Deal.guarantee_id, isouter=True)
            .where(Deal.id == deal_id)
        )
        row = result.first()

    if not row:
        await callback.message.answer("Сделка не найдена.")
        return

    deal, ad, game, seller, buyer, guarantor = row
    game_name = game.name if game else "-"
    ad_title = ad.title if ad else "-"
    description = ad.description if ad else "-"
    payment = ad.payment_methods if ad and ad.payment_methods else "-"
    seller_label = (
        f"{seller.id} (@{seller.username})" if seller.username else str(seller.id)
    )
    buyer_label = f"{buyer.id} (@{buyer.username})" if buyer.username else str(buyer.id)
    guarantor_label = (
        f"{guarantor.id} (@{guarantor.username})"
        if guarantor and guarantor.username
        else (str(guarantor.id) if guarantor else "-")
    )

    text = (
        f"Сделка #{deal.id}\n"
        f"Статус: {deal.status}\n"
        f"Тип: {deal.deal_type}\n"
        f"Игра: {game_name}\n"
        f"Лот: {ad_title}\n"
        f"Описание: {description}\n"
        f"Цена: {deal.price or '-'} ₽\n"
        f"Комиссия: {deal.fee or 0} ₽\n"
        f"Оплата: {payment}\n"
        f"Продавец: {seller_label}\n"
        f"Покупатель: {buyer_label}\n"
        f"Гарант: {guarantor_label}\n"
        f"Создана: {deal.created_at.strftime('%Y-%m-%d %H:%M')}"
    )
    await callback.message.answer(text)


@router.callback_query(F.data == "owner:design_tasks")
async def owner_tasks(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner tasks.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_admin(user.role) and not is_owner(
        user.role, settings.owner_ids, user.id
    ):
        await callback.answer("Нет доступа.")
        return
    await callback.message.answer("Задачи дизайнеру. Отправьте /task user_id Название")
    await callback.answer()


@router.message(F.text.startswith("/task "))
async def owner_task_create(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner task create.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        owner = await get_or_create_user(session, message.from_user)
        if not _is_admin(owner.role) and not is_owner(
            owner.role, settings.owner_ids, owner.id
        ):
            return

        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Формат: /task user_id Название")
            return
        user_id = int(parts[1])
        title = parts[2]
        task = StaffTask(
            assignee_id=user_id,
            creator_id=owner.id,
            title=title,
        )
        session.add(task)
        await session.commit()

    await message.answer("Задача создана.")
    await _log_admin(
        message.bot,
        settings,
        f"Задача дизайнеру: {user_id} '{title}' (кто: {owner.id})",
    )


@router.callback_query(F.data == "designer:tasks")
async def designer_tasks(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle designer tasks.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(StaffTask)
            .where(StaffTask.assignee_id == callback.from_user.id)
            .order_by(StaffTask.id.desc())
            .limit(20)
        )
        tasks = result.scalars().all()

    if not tasks:
        await callback.message.answer("Задач нет.")
        await callback.answer()
        return

    for task in tasks:
        text = f"Задача #{task.id}\n{task.title}\nСтатус: {task.status}"
        await callback.message.answer(text, reply_markup=task_kb(task.id, False))
    await callback.answer()


@router.callback_query(F.data.startswith("task_done:"))
async def task_done(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    """Handle task done.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    task_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(StaffTask).where(StaffTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            await callback.answer("Задача не найдена.")
            return
        if task.assignee_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        task.status = "done"
        await session.commit()
    await callback.message.answer("Задача отмечена как выполненная.")
    await callback.answer()


@router.callback_query(F.data.startswith("deal_close:"))
async def deal_close_legacy(callback: CallbackQuery) -> None:
    """Handle deal close legacy.

    Args:
        callback: Value for callback.
    """
    deal_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"Закрыть сделку #{deal_id}?",
        reply_markup=confirm_deal_action_kb("deal_close", deal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal_close_req:"))
async def deal_close_req(callback: CallbackQuery) -> None:
    """Handle deal close req.

    Args:
        callback: Value for callback.
    """
    deal_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"Закрыть сделку #{deal_id}?",
        reply_markup=confirm_deal_action_kb("deal_close", deal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal_cancel_req:"))
async def deal_cancel_req(callback: CallbackQuery) -> None:
    """Handle deal cancel req.

    Args:
        callback: Value for callback.
    """
    deal_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"Отменить сделку #{deal_id}?",
        reply_markup=confirm_deal_action_kb("deal_cancel", deal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal_cancel:"))
async def deal_cancel_legacy(callback: CallbackQuery) -> None:
    """Handle deal cancel legacy.

    Args:
        callback: Value for callback.
    """
    deal_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"Отменить сделку #{deal_id}?",
        reply_markup=confirm_deal_action_kb("deal_cancel", deal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal_close_yes:"))
async def deal_close_yes(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle deal close yes.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or deal.guarantee_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        if deal.status == "closed":
            await callback.answer("Сделка уже закрыта.")
            return
        deal.status = "closed"
        deal.closed_at = datetime.now(timezone.utc)
        await apply_trust_event(
            session,
            deal.buyer_id,
            "deal_success",
            2,
            "???????? ??????",
            ref_type="deal",
            ref_id=deal.id,
        )
        await apply_trust_event(
            session,
            deal.seller_id,
            "deal_success",
            2,
            "???????? ??????",
            ref_type="deal",
            ref_id=deal.id,
        )
        if deal.price:
            reward = Decimal(str(deal.price)) * Decimal("0.001")
            result = await session.execute(
                select(User).where(User.id == deal.seller_id)
            )
            seller = result.scalar_one_or_none()
            if seller:
                seller.balance = (seller.balance or 0) + reward
                session.add(
                    WalletTransaction(
                        user_id=seller.id,
                        amount=reward,
                        type="deal_reward",
                        description=f"Сделка #{deal.id}",
                    )
                )
        await session.commit()
    await callback.message.answer(f"Сделка #{deal_id} закрыта.")
    review_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оставить отзыв",
                    callback_data=f"review_start:{deal_id}",
                )
            ]
        ]
    )
    await callback.bot.send_message(
        deal.buyer_id,
        f"Сделка #{deal_id} закрыта. Оставьте отзыв о гаранте и второй стороне.",
        reply_markup=review_kb,
    )
    await callback.bot.send_message(
        deal.seller_id,
        f"Сделка #{deal_id} закрыта. Оставьте отзыв о гаранте и второй стороне.",
        reply_markup=review_kb,
    )
    await _log_admin(
        callback.bot,
        settings,
        f"Сделка закрыта #{deal_id} (гарант {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal_cancel_yes:"))
async def deal_cancel(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle deal cancel.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or deal.guarantee_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        deal.status = "canceled"
        await apply_trust_event(
            session,
            deal.buyer_id,
            "deal_cancel",
            -3,
            "?????? ??????",
            ref_type="deal",
            ref_id=deal.id,
        )
        await apply_trust_event(
            session,
            deal.seller_id,
            "deal_cancel",
            -3,
            "?????? ??????",
            ref_type="deal",
            ref_id=deal.id,
        )
        await _release_deal_room(session, deal)
        await session.commit()
    await callback.message.answer(f"Сделка #{deal_id} отменена.")
    await _log_admin(
        callback.bot,
        settings,
        f"Сделка отменена #{deal_id} (гарант {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(
    F.data.startswith("deal_close_no:") | F.data.startswith("deal_cancel_no:")
)
async def deal_action_no(callback: CallbackQuery) -> None:
    """Handle deal action no.

    Args:
        callback: Value for callback.
    """
    await callback.message.answer("Действие отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("deal_dispute:"))
async def deal_dispute(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle deal dispute.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or deal.guarantee_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        dispute = Dispute(
            deal_id=deal_id,
            reporter_id=callback.from_user.id,
            description="Спор открыт гарантом.",
        )
        session.add(dispute)
        await session.commit()

    await callback.message.answer("Спор открыт, отправлено в админ-чат.")
    await _log_admin(
        callback.bot,
        settings,
        f"Спор открыт #{dispute.id} по сделке #{deal_id} (гарант {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mod_export:"))
async def export_moderation(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle export moderation.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    status = callback.data.split(":")[1]
    async with sessionmaker() as session:
        query = select(Ad).order_by(Ad.id.desc()).limit(200)
        if status != "all":
            query = query.where(Ad.moderation_status == status)
        result = await session.execute(query)
        ads = result.scalars().all()

    if not ads:
        await callback.message.answer("Журнал модерации пуст.")
        await callback.answer()
        return

    lines = [f"Журнал модерации ({status}, последние 200):"]
    for ad in ads:
        lines.append(
            f"#{ad.id} | seller={ad.seller_id} | status={ad.moderation_status} | created={ad.created_at}"
        )
    data = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(data, filename="moderation_log.txt")
    await callback.message.answer_document(file)
    await _log_admin(
        callback.bot,
        settings,
        f"Экспорт модерации (кто: {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("complaint_export:"))
async def export_complaints(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle export complaints.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    status = callback.data.split(":")[1]
    async with sessionmaker() as session:
        query = select(Complaint).order_by(Complaint.id.desc()).limit(200)
        if status != "all":
            query = query.where(Complaint.status == status)
        result = await session.execute(query)
        complaints = result.scalars().all()

    if not complaints:
        await callback.message.answer("Журнал жалоб пуст.")
        await callback.answer()
        return

    lines = [f"Журнал жалоб ({status}, последние 200):"]
    for complaint in complaints:
        lines.append(
            f"#{complaint.id} | ad={complaint.ad_id} | reporter={complaint.reporter_id} | status={complaint.status} | created={complaint.created_at}\n{complaint.reason}"
        )
    data = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(data, filename="complaints_log.txt")
    await callback.message.answer_document(file)
    await _log_admin(
        callback.bot,
        settings,
        f"Экспорт жалоб (кто: {callback.from_user.id})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("broadcast_approve:"))
async def broadcast_approve(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle broadcast approve.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    request_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(BroadcastRequest).where(BroadcastRequest.id == request_id)
        )
        req = result.scalar_one_or_none()
        if not req or req.status != "pending":
            await callback.answer("Запрос не найден.")
            return
        req_text = req.text
        req_kind = req.kind
        req_creator = req.creator_id
        req.status = "approved"
        await session.commit()

        room_result = await session.execute(select(DealRoom.chat_id))
        room_ids = {
            room_id
            for room_id in room_result.scalars().all()
            if room_id is not None
        }

        result = await session.execute(select(User.id))
        user_ids = [
            user_id
            for user_id in result.scalars().all()
            if user_id not in room_ids
        ]

    await callback.answer("Рассылка запущена.")


    async def _run_broadcast() -> None:
        sent = 0
        failed = 0
        for user_id in user_ids:
            ok = await _send_broadcast_message(callback.bot, user_id, req_text)
            if ok:
                sent += 1
            else:
                failed += 1

        await callback.message.answer(
            f"Рассылка одобрена. Отправлено: {sent}. " f"Ошибки: {failed}."
        )
    asyncio.create_task(_run_broadcast())


@router.callback_query(F.data.startswith("broadcast_reject:"))
async def broadcast_reject(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle broadcast reject.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not _is_moderator(user.role):
        await callback.answer("Нет доступа.")
        return
    request_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(BroadcastRequest).where(BroadcastRequest.id == request_id)
        )
        req = result.scalar_one_or_none()
        if not req or req.status != "pending":
            await callback.answer("Запрос не найден.")
            return
        req.status = "rejected"
        if req.cost and req.cost > 0:
            result = await session.execute(
                select(User).where(User.id == req.creator_id)
            )
            creator = result.scalar_one_or_none()
            if creator:
                creator.balance = (creator.balance or 0) + req.cost
                session.add(
                    WalletTransaction(
                        user_id=creator.id,
                        amount=req.cost,
                        type="broadcast_refund",
                        description=f"Возврат за рассылку #{req.id}",
                    )
                )
        await session.commit()

    await callback.message.answer("Рассылка отклонена.")
    await _log_admin(
        callback.bot,
        settings,
        f"Рассылка #{request_id} отклонена",
    )
    await callback.answer()


@router.message(F.text.startswith("/broadcast "))
async def staff_broadcast(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle staff broadcast.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        sender = await get_or_create_user(session, message.from_user)
        if not _is_admin(sender.role) and not is_owner(
            sender.role, settings.owner_ids, sender.id
        ):
            return
        text = message.text.split(" ", 1)[1].strip()
        if not text:
            await message.answer("Формат: /broadcast текст")
            return
        await create_broadcast_request(
            session,
            message.bot,
            settings,
            creator_id=sender.id,
            text=text,
            kind="staff",
            cost=0,
        )
    await message.answer("Запрос рассылки отправлен на модерацию.")


@router.message(F.text.startswith("/set_vip"))
async def set_vip(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Set vip.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        admin = await get_or_create_user(session, message.from_user)
        if not _is_admin(admin.role) and not is_owner(
            admin.role, settings.owner_ids, admin.id
        ):
            return
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("Формат: /set_vip user_id дни")
            return
        target_token = parts[1]
        days_raw = parts[2]
        try:
            days = int(days_raw)
        except ValueError:
            await message.answer("Неверное количество дней.")
            return
        target_id = await _resolve_user_id(session, target_token)
        if not target_id:
            await message.answer("Пользователь не найден.")
            return
        result = await session.execute(select(User).where(User.id == target_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Пользователь не найден.")
            return
        if days <= 0:
            user.vip_until = None
            await session.commit()
            await message.answer(f"VIP отключен для {target_id}.")
            return
        user.vip_until = datetime.utcnow() + timedelta(days=days)
        await session.commit()
    await message.answer(
        f"VIP активен для {target_id} до {user.vip_until.strftime('%Y-%m-%d %H:%M')}"
    )


@router.message(F.text.startswith("/create_deal"))
async def create_deal_manual(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Create deal manual.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        guarantor = await get_or_create_user(session, message.from_user)
        if not _is_guarantor(guarantor.role) and not is_owner(
            guarantor.role, settings.owner_ids, guarantor.id
        ):
            return
        if guarantor.role == "guarantor" and not guarantor.on_shift:
            await message.answer("Вы не на смене.")
            return

        parts = message.text.split()
        reply_user = None
        if message.reply_to_message:
            if message.reply_to_message.forward_from:
                reply_user = message.reply_to_message.forward_from
            elif message.reply_to_message.from_user:
                reply_user = message.reply_to_message.from_user

        if reply_user:
            if len(parts) < 3:
                await message.answer("Формат: /create_deal seller price [type] [addon]")
                return
            buyer_id = reply_user.id
            result = await session.execute(select(User).where(User.id == buyer_id))
            buyer_user = result.scalar_one_or_none()
            if not buyer_user:
                await message.answer(
                    "Пользователь не найден. Попросите его нажать /start."
                )
                return
            seller_token = parts[1]
            price_raw = parts[2]
            deal_type = parts[3] if len(parts) > 3 else "buy"
            addon_raw = parts[4] if len(parts) > 4 else None
        else:
            if len(parts) < 4:
                await message.answer(
                    "Формат: /create_deal buyer seller price [type] [addon]"
                )
                return
            buyer_token = parts[1]
            seller_token = parts[2]
            price_raw = parts[3]
            deal_type = parts[4] if len(parts) > 4 else "buy"
            addon_raw = parts[5] if len(parts) > 5 else None
            buyer_id = await _resolve_user_id(session, buyer_token)

        seller_id = await _resolve_user_id(session, seller_token)
        if not buyer_id or not seller_id:
            await message.answer("Пользователь не найден. Попросите его нажать /start.")
            return
        result = await session.execute(select(User).where(User.id == seller_id))
        seller_user = result.scalar_one_or_none()
        if not seller_user:
            await message.answer("Пользователь не найден. Попросите его нажать /start.")
            return

        try:
            price = Decimal(price_raw.replace(",", "."))
        except Exception:
            await message.answer("Неверная цена.")
            return

        if deal_type not in {
            "buy",
            "contact",
            "exchange",
            "exchange_with_addon",
            "installment",
        }:
            await message.answer(
                "Неверный тип. Используйте: buy, contact, exchange, exchange_with_addon, installment."
            )
            return

        addon_amount = None
        if deal_type == "exchange_with_addon":
            if not addon_raw:
                await message.answer("Для exchange_with_addon нужна сумма доплаты.")
                return
            try:
                addon_amount = Decimal(addon_raw.replace(",", "."))
            except Exception:
                await message.answer("Неверная сумма доплаты.")
                return

        trust_score = await get_trust_score(session, seller_user.id)
        fee = calculate_fee(price, deal_type, addon_amount, trust_score=trust_score)
        if free_fee_active(seller_user.free_fee_until):
            fee = Decimal("0")
        deal = Deal(
            ad_id=None,
            buyer_id=buyer_id,
            seller_id=seller_id,
            guarantee_id=guarantor.id,
            status="in_progress",
            deal_type=deal_type,
            price=price,
            fee=fee,
        )
        session.add(deal)
        await session.commit()
        room, room_error = await _assign_deal_room(session, deal)
        await session.commit()

    await message.answer(f"Ручная сделка создана #{deal.id}.")
    await message.bot.send_message(
        buyer_id,
        f"Создана ручная сделка #{deal.id}.",
        reply_markup=deal_after_take_kb(
            deal.id,
            role="buyer",
            guarantor_id=guarantor.id,
        ),
    )
    await message.bot.send_message(
        seller_id,
        f"Создана ручная сделка #{deal.id}.",
        reply_markup=deal_after_take_kb(
            deal.id,
            role="seller",
            guarantor_id=guarantor.id,
        ),
    )
    await message.bot.send_message(
        guarantor.id,
        f"✅ Вы назначены гарантом сделки #{deal.id}.",
        reply_markup=deal_after_take_kb(
            deal.id,
            role="guarantor",
            guarantor_id=guarantor.id,
        ),
    )
    if room_error:
        await message.bot.send_message(
            guarantor.id,
            f"Deal #{deal.id} has no room yet. {room_error}",
        )
        chat_id, topic_id = get_admin_target(settings)
        if chat_id:
            await message.bot.send_message(
                chat_id,
                f"Deal #{deal.id} created, but no free rooms available.",
                message_thread_id=topic_id,
            )
    elif room and room.invite_link:
        await message.bot.send_message(
            guarantor.id,
            (
                f"Deal #{deal.id} room assigned. "
                "Press “Open chat” to release the link to participants."
            ),
        )

    await _notify_room_pool_low(message.bot, settings, sessionmaker)
    await _log_admin(
        message.bot,
        settings,
        f"Ручная сделка #{deal.id} создана ({message.from_user.id})",
    )


async def _log_admin(bot, settings: Settings, text: str) -> None:
    """Handle log admin.

    Args:
        bot: Value for bot.
        settings: Value for settings.
        text: Value for text.
    """
    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        return
    await bot.send_message(
        chat_id,
        text,
        message_thread_id=topic_id,
    )


def _can_manage_trust(user: User, settings: Settings) -> bool:
    """Handle can manage trust.

    Args:
        user: Value for user.
        settings: Value for settings.

    Returns:
        Return value.
    """
    return user.role in {"owner", "admin", "moderator", "guarantor"} or is_owner(
        user.role, settings.owner_ids, user.id
    )


@router.message(F.text.startswith("/trust_freeze"))
async def trust_freeze(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle trust freeze.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        actor = await get_or_create_user(session, message.from_user)
        if not _can_manage_trust(actor, settings):
            return
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            await message.answer("Использование: /trust_freeze user_id [причина]")
            return
        target_id = await _resolve_user_id(session, parts[1])
        if not target_id:
            await message.answer("Пользователь не найден.")
            return
        reason = parts[2] if len(parts) > 2 else "Trust заморожен"
        await set_trust_frozen(session, target_id, True)
        await apply_trust_event(
            session,
            target_id,
            "trust_freeze",
            0,
            reason,
            ref_type="trust",
            ref_id=target_id,
            allow_duplicate=True,
        )
    await message.answer(f"Trust заморожен для {target_id}.")


@router.message(F.text.startswith("/trust_unfreeze"))
async def trust_unfreeze(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle trust unfreeze.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        actor = await get_or_create_user(session, message.from_user)
        if not _can_manage_trust(actor, settings):
            return
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            await message.answer("Использование: /trust_unfreeze user_id [причина]")
            return
        target_id = await _resolve_user_id(session, parts[1])
        if not target_id:
            await message.answer("Пользователь не найден.")
            return
        reason = parts[2] if len(parts) > 2 else "Trust разморожен"
        await set_trust_frozen(session, target_id, False)
        await apply_trust_event(
            session,
            target_id,
            "trust_unfreeze",
            0,
            reason,
            ref_type="trust",
            ref_id=target_id,
            allow_duplicate=True,
        )
    await message.answer(f"Trust разморожен для {target_id}.")


@router.message(F.text.startswith("/trust_rollback"))
async def trust_rollback(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle trust rollback.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        actor = await get_or_create_user(session, message.from_user)
        if not _can_manage_trust(actor, settings):
            return
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /trust_rollback event_id")
            return
        try:
            event_id = int(parts[1])
        except ValueError:
            await message.answer("Неверный event_id.")
            return
        ok = await rollback_trust_event(session, event_id)
        await message.answer("Событие откатано." if ok else "Событие не найдено.")


@router.message(F.text.startswith("/verify_user"))
async def verify_user(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle verify user.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        actor = await get_or_create_user(session, message.from_user)
        if not _can_manage_trust(actor, settings):
            return
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /verify_user user_id")
            return
        target_id = await _resolve_user_id(session, parts[1])
        if not target_id:
            await message.answer("Пользователь не найден.")
            return
        result = await session.execute(select(User).where(User.id == target_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Пользователь не найден.")
            return
        if not user.verified:
            user.verified = True
            await apply_trust_event(
                session,
                target_id,
                "verification",
                5,
                "Верификация",
                ref_type="verify",
                ref_id=target_id,
            )
            await session.commit()
        await message.answer(f"Верификация включена для {target_id}.")


@router.message(F.text.startswith("/unverify_user"))
async def unverify_user(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle unverify user.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        actor = await get_or_create_user(session, message.from_user)
        if not _can_manage_trust(actor, settings):
            return
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /unverify_user user_id")
            return
        target_id = await _resolve_user_id(session, parts[1])
        if not target_id:
            await message.answer("Пользователь не найден.")
            return
        result = await session.execute(select(User).where(User.id == target_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Пользователь не найден.")
            return
        if user.verified:
            user.verified = False
            await apply_trust_event(
                session,
                target_id,
                "unverify",
                -5,
                "Снята верификация",
                ref_type="verify",
                ref_id=target_id,
                allow_duplicate=True,
            )
            await session.commit()
        await message.answer(f"Верификация снята для {target_id}.")


@router.message(F.text.startswith("/resolve_dispute"))
async def resolve_dispute(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Resolve dispute.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        actor = await get_or_create_user(session, message.from_user)
        if not _can_manage_trust(actor, settings):
            return
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer(
                "Использование: /resolve_dispute dispute_id buyer|seller"
            )
            return
        try:
            dispute_id = int(parts[1])
        except ValueError:
            await message.answer("Неверный dispute_id.")
            return
        winner_role = parts[2].lower()

        result = await session.execute(select(Dispute).where(Dispute.id == dispute_id))
        dispute = result.scalar_one_or_none()
        if not dispute:
            await message.answer("Спор не найден.")
            return
        if dispute.status != "open":
            await message.answer("Спор уже закрыт.")
            return
        result = await session.execute(select(Deal).where(Deal.id == dispute.deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await message.answer("Сделка не найдена.")
            return

        if winner_role == "buyer":
            winner_id = deal.buyer_id
            loser_id = deal.seller_id
        elif winner_role == "seller":
            winner_id = deal.seller_id
            loser_id = deal.buyer_id
        else:
            await message.answer("Укажи winner: buyer или seller.")
            return

        dispute.winner_id = winner_id
        dispute.status = "resolved"
        await apply_trust_event(
            session,
            loser_id,
            "dispute_lost",
            -15,
            "Проигранный спор",
            ref_type="dispute",
            ref_id=dispute.id,
        )
        await session.commit()
    await message.answer(f"Спор #{dispute_id} решен в пользу {winner_role}.")


def _trust_menu_kb() -> InlineKeyboardMarkup:
    """Handle trust menu kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧾 Последние события",
                    callback_data="trust:recent",
                ),
                InlineKeyboardButton(
                    text="🔎 По пользователю",
                    callback_data="trust:by_user",
                ),
            ]
        ]
    )


def _trust_event_kb(event_id: int) -> InlineKeyboardMarkup:
    """Handle trust event kb.

    Args:
        event_id: Value for event_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Откатить",
                    callback_data=f"trust:rollback:{event_id}",
                )
            ]
        ]
    )


def _trust_event_text(event: TrustEvent) -> str:
    """Handle trust event text.

    Args:
        event: Value for event.

    Returns:
        Return value.
    """
    status = "применено" if event.applied else "не применено"
    if event.reversed:
        status = "откатано"
    return (
        f"<b>Trust событие #{event.id}</b>\n"
        f"Пользователь: {event.user_id}\n"
        f"Тип: {event.event_type}\n"
        f"Изменение: {event.delta}\n"
        f"Причина: {event.reason or "-"}\n"
        f"Статус: {status}\n"
        f"Время: {event.created_at.strftime('%Y-%m-%d %H:%M')}"
    )


@router.callback_query(F.data == "owner:trust")
async def owner_trust_panel(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle owner trust panel.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    text = (
        "<b>🧭 Trust Score — панель управления</b>\n\n"
        "🎯 Зачем: единый индикатор доверия, влияет на комиссию.\n"
        "🛡 Порог: новые аккаунты ограничены по капу.\n"
        "📝 Логи: все изменения пишутся в события.\n\n"
        "⚠️ Важно:\n"
        "• Откатить можно только событие Trust.\n"
        "• Заморозка фиксирует балл до ручной разморозки.\n\n"
        "🧰 Команды:\n"
        "/trust_freeze user_id [причина]\n"
        "/trust_unfreeze user_id [причина]\n"
        "/trust_rollback event_id\n"
        "/verify_user user_id\n"
        "/unverify_user user_id\n"
        "/resolve_dispute dispute_id buyer|seller"
    )
    await callback.message.answer(text, reply_markup=_trust_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "trust:recent")
async def trust_recent(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle trust recent.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    async with sessionmaker() as session:
        result = await session.execute(
            select(TrustEvent).order_by(TrustEvent.id.desc()).limit(15)
        )
        events = result.scalars().all()
    if not events:
        await callback.message.answer("Событий нет.")
        await callback.answer()
        return
    for event in events:
        kb = _trust_event_kb(event.id) if not event.reversed else None
        await callback.message.answer(_trust_event_text(event), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "trust:by_user")
async def trust_by_user_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle trust by user prompt.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    await state.set_state(TrustByUserStates.user_id)
    await callback.message.answer("Введите user_id или @username:")
    await callback.answer()


@router.message(TrustByUserStates.user_id)
async def trust_by_user(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle trust by user.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Введите user_id или @username.")
        return
    async with sessionmaker() as session:
        target_id = await _resolve_user_id(session, raw)
        if not target_id:
            await message.answer("Пользователь не найден.")
            await state.clear()
            return
        result = await session.execute(
            select(TrustState).where(TrustState.user_id == target_id)
        )
        trust_state = result.scalar_one_or_none()
        score = trust_state.score if trust_state else 0
        frozen = trust_state.frozen if trust_state else False
        cap = trust_state.cap if trust_state else 100

        result = await session.execute(
            select(TrustEvent)
            .where(TrustEvent.user_id == target_id)
            .order_by(TrustEvent.id.desc())
            .limit(10)
        )
        events = result.scalars().all()

    text = (
        f"<b>Trust пользователя {target_id}</b>\n"
        f"Счет: {score}/{cap}\n"
        f"Заморозка: {'да' if frozen else 'нет'}"
    )
    await message.answer(text)
    for event in events:
        kb = _trust_event_kb(event.id) if not event.reversed else None
        await message.answer(_trust_event_text(event), reply_markup=kb)
    await state.clear()


@router.callback_query(F.data.startswith("trust:rollback:"))
async def trust_rollback_inline(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle trust rollback inline.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    user = await _load_user(sessionmaker, callback.from_user)
    if not is_owner(user.role, settings.owner_ids, user.id):
        await callback.answer("Нет доступа.")
        return
    event_id = int(callback.data.split(":")[2])
    async with sessionmaker() as session:
        ok = await rollback_trust_event(session, event_id)
    await callback.answer("Откат выполнен." if ok else "Событие не найдено.")
