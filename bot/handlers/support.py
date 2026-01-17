"""Module for support functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import SupportMessage, SupportTicket, User
from bot.handlers.helpers import get_or_create_user
from bot.utils.admin_target import get_admin_target
from bot.utils.roles import is_owner

router = Router()


class SupportStates(StatesGroup):
    """Represent SupportStates.

    Attributes:
        active: Attribute value.
    """

    active = State()


class SupportReplyStates(StatesGroup):
    """Represent SupportReplyStates.

    Attributes:
        waiting: Attribute value.
    """

    waiting = State()


def _reply_kb(ticket_id: int) -> InlineKeyboardMarkup:
    """Handle reply kb.

    Args:
        ticket_id: Value for ticket_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Ответить", callback_data=f"support_reply:{ticket_id}"
                ),
                InlineKeyboardButton(
                    text="🧾 История", callback_data=f"support_history:{ticket_id}"
                ),
                InlineKeyboardButton(
                    text="✅ Закрыть", callback_data=f"support_close:{ticket_id}"
                ),
            ]
        ]
    )


def _is_moderator(role: str) -> bool:
    """Handle is moderator.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    return role in {"owner", "admin", "moderator"}


async def _load_support_recipients(
    sessionmaker: async_sessionmaker, settings: Settings
) -> list[int]:
    """Load support recipients (moderators + owners)."""
    ids: set[int] = set(settings.owner_ids or [])
    async with sessionmaker() as session:
        result = await session.execute(select(User.id).where(User.role == "moderator"))
        ids.update(result.scalars().all())
        result = await session.execute(select(User.id).where(User.role == "owner"))
        ids.update(result.scalars().all())
    return sorted(ids)


def _assignee_label(user: User | None, fallback_id: int | None) -> str:
    """Format a readable assignee label."""
    if user and user.username:
        return f"@{user.username}"
    if user:
        return str(user.id)
    if fallback_id is not None:
        return str(fallback_id)
    return "-"


async def _ticket_history_text(sessionmaker: async_sessionmaker, ticket_id: int) -> str:
    """Handle ticket history text.

    Args:
        sessionmaker: Value for sessionmaker.
        ticket_id: Value for ticket_id.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket_id)
            .order_by(SupportMessage.id.asc())
        )
        messages = result.scalars().all()

    lines = [f"История тикета #{ticket_id}"]
    for msg in messages:
        when = msg.created_at.strftime("%Y-%m-%d %H:%M")
        content = msg.text or f"[{msg.media_type or 'media'}]"
        lines.append(f"[{when}] {msg.sender_id}: {content}")
    return "\n".join(lines)


async def _start_support_dialog(state: FSMContext, message: Message) -> None:
    await state.clear()
    await state.set_state(SupportStates.active)
    await message.answer(
        "Опишите проблему одним сообщением. Для выхода напишите /exit."
    )


@router.message(F.text == "/support")
async def support_command(message: Message, state: FSMContext) -> None:
    """Handle support command."""
    if message.chat.type != "private":
        await message.answer("Команда доступна только в ЛС.")
        return
    await _start_support_dialog(state, message)


@router.callback_query(F.data == "support:start")
async def support_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle support start.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await _start_support_dialog(state, callback.message)
    await callback.answer()


@router.message(SupportStates.active)
async def support_message(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle support message.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if message.text and message.text.strip() == "/exit":
        await state.clear()
        await message.answer("✅ Диалог поддержки завершен.")
        return

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        result = await session.execute(
            select(SupportTicket).where(
                SupportTicket.user_id == user.id, SupportTicket.status == "open"
            )
        )
        ticket = result.scalar_one_or_none()
        last_message = message.text or "[media]"
        if not ticket:
            ticket = SupportTicket(user_id=user.id, last_message=last_message)
            session.add(ticket)
        else:
            ticket.last_message = last_message
        await session.flush()

        media_type = None
        file_id = None
        if message.photo:
            media_type = "photo"
            file_id = message.photo[-1].file_id
        elif message.video:
            media_type = "video"
            file_id = message.video.file_id
        elif message.document:
            media_type = "document"
            file_id = message.document.file_id

        session.add(
            SupportMessage(
                ticket_id=ticket.id,
                sender_id=user.id,
                text=message.text,
                media_type=media_type,
                file_id=file_id,
            )
        )
        await session.commit()

    recipient_ids = await _load_support_recipients(sessionmaker, settings)
    user_label = f"{user.id} (@{user.username})" if user.username else str(user.id)
    text = (
        f"🆘 Тикет #{ticket.id}\n"
        f"👤 Пользователь: {user_label}\n"
        f"📝 Сообщение: {message.text or '[вложение]'}"
    )
    for recipient_id in recipient_ids:
        try:
            if message.photo:
                await message.bot.send_photo(
                    recipient_id,
                    message.photo[-1].file_id,
                    caption=text,
                    reply_markup=_reply_kb(ticket.id),
                )
            elif message.video:
                await message.bot.send_video(
                    recipient_id,
                    message.video.file_id,
                    caption=text,
                    reply_markup=_reply_kb(ticket.id),
                )
            elif message.document:
                await message.bot.send_document(
                    recipient_id,
                    message.document.file_id,
                    caption=text,
                    reply_markup=_reply_kb(ticket.id),
                )
            else:
                await message.bot.send_message(
                    recipient_id, text, reply_markup=_reply_kb(ticket.id)
                )
        except Exception:
            continue

    await message.answer("✅ Обращение отправлено в поддержку.")


@router.callback_query(F.data.startswith("support_reply:"))
async def support_reply_start(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle support reply start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    ticket_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return
        result = await session.execute(
            select(SupportTicket).where(SupportTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket or ticket.status != "open":
            await callback.answer("Тикет не найден или закрыт.")
            return
        if ticket.assignee_id and ticket.assignee_id != user.id:
            assignee = await session.get(User, ticket.assignee_id)
            label = _assignee_label(assignee, ticket.assignee_id)
            await callback.answer(f"Тикет уже в работе у {label}.")
            return
        if ticket.assignee_id is None:
            ticket.assignee_id = user.id
            await session.commit()
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(SupportReplyStates.waiting)
    await callback.message.answer(f"Ответ на тикет #{ticket_id}. Напишите сообщение.")
    await callback.answer()


@router.message(SupportReplyStates.waiting)
async def support_reply_send(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle support reply send.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await state.clear()
        await message.answer("⏱️ Сеанс истек.")
        return

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await state.clear()
            return
        result = await session.execute(
            select(SupportTicket).where(SupportTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket or ticket.status != "open":
            await message.answer("Тикет не найден или закрыт.")
            await state.clear()
            return
        if ticket.assignee_id and ticket.assignee_id != user.id:
            assignee = await session.get(User, ticket.assignee_id)
            label = _assignee_label(assignee, ticket.assignee_id)
            await message.answer(f"Тикет уже в работе у {label}.")
            await state.clear()
            return
        if ticket.assignee_id is None:
            ticket.assignee_id = user.id
        media_type = None
        file_id = None
        if message.photo:
            media_type = "photo"
            file_id = message.photo[-1].file_id
        elif message.video:
            media_type = "video"
            file_id = message.video.file_id
        elif message.document:
            media_type = "document"
            file_id = message.document.file_id

        session.add(
            SupportMessage(
                ticket_id=ticket.id,
                sender_id=user.id,
                text=message.text,
                media_type=media_type,
                file_id=file_id,
            )
        )
        await session.commit()

        if message.photo:
            await message.bot.send_photo(
                ticket.user_id,
                message.photo[-1].file_id,
                caption="💬 Ответ поддержки",
            )
        elif message.video:
            await message.bot.send_video(
                ticket.user_id,
                message.video.file_id,
                caption="💬 Ответ поддержки",
            )
        elif message.document:
            await message.bot.send_document(
                ticket.user_id,
                message.document.file_id,
                caption="💬 Ответ поддержки",
            )
        else:
            await message.bot.send_message(
                ticket.user_id, f"💬 Ответ поддержки:\n{message.text}"
            )

    await state.clear()
    await message.answer("✅ Ответ отправлен.")


@router.callback_query(F.data.startswith("support_history:"))
async def support_history(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle support history.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    ticket_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return

    history = await _ticket_history_text(sessionmaker, ticket_id)
    await callback.message.answer(history)
    await callback.answer()


@router.callback_query(F.data.startswith("support_close:"))
async def support_close_btn(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle support close btn.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    ticket_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return
        result = await session.execute(
            select(SupportTicket).where(SupportTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            await callback.answer("Тикет не найден.")
            return
        ticket.status = "closed"
        ticket.assignee_id = None
        await session.commit()

    await _send_ticket_to_admin_chat(callback.bot, sessionmaker, settings, ticket_id)
    await callback.message.answer("✅ Тикет закрыт.")
    await callback.answer()


@router.message(F.text.startswith("/support_close"))
async def support_close(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle support close.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            return

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Формат: /support_close ticket_id")
            return
        ticket_id = int(parts[1])
        result = await session.execute(
            select(SupportTicket).where(SupportTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            await message.answer("Тикет не найден.")
            return
        ticket.status = "closed"
        ticket.assignee_id = None
        await session.commit()

    await _send_ticket_to_admin_chat(message.bot, sessionmaker, settings, ticket_id)
    await message.answer("✅ Тикет закрыт.")


async def _send_ticket_to_admin_chat(
    bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    ticket_id: int,
) -> None:
    """Handle send ticket to admin chat.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
        ticket_id: Value for ticket_id.
    """
    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        return

    history = await _ticket_history_text(sessionmaker, ticket_id)
    data = history.encode("utf-8")
    file = BufferedInputFile(data, filename=f"ticket_{ticket_id}.txt")
    await bot.send_document(
        chat_id,
        file,
        message_thread_id=topic_id,
        caption=f"🧾 Тикет #{ticket_id} закрыт. Полная история во вложении.",
    )
