"""Module for chat moderation functionality."""

from __future__ import annotations

import asyncio
import html
import json
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    ModerationCase,
    ModerationChat,
    ModerationCustomEmoji,
    ModerationCustomEmojiPack,
    ModerationMemberEvent,
    ModerationRestriction,
    ModerationStickerPack,
    ModerationWord,
    User,
)
from bot.keyboards.common import referral_kb
from bot.utils.admin_target import get_admin_target
from bot.utils.moderation import contains_blacklist
from bot.utils.texts import CHAT_WELCOME_TEXT
from bot.services.trust import apply_trust_event

router = Router()


def _parse_target_and_reason(
    message: Message, args: list[str]
) -> tuple[int | None, str]:
    """Handle parse target and reason.

    Args:
        message: Value for message.
        args: Value for args.

    Returns:
        Return value.
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        reason = " ".join(args).strip() if args else "-"
        return message.reply_to_message.from_user.id, reason or "-"
    if not args:
        return None, ""
    raw_id = args[0].lstrip("@")
    if not raw_id.isdigit():
        return None, ""
    reason = " ".join(args[1:]).strip() if len(args) > 1 else "-"
    return int(raw_id), reason or "-"


def _parse_duration(value: str) -> timedelta | None:
    """Handle parse duration.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    token = value.strip().lower()
    if not token:
        return None
    unit = token[-1]
    number = token[:-1] if unit.isalpha() else token
    if not number.isdigit():
        return None
    amount = int(number)
    if amount <= 0:
        return None
    if unit in {"h", "ч"}:
        return timedelta(hours=amount)
    if unit in {"d", "д"}:
        return timedelta(days=amount)
    return timedelta(hours=amount)


def _format_tg_user(user) -> str:
    """Handle format tg user.

    Args:
        user: Value for user.

    Returns:
        Return value.
    """
    if not user:
        return "-"
    username = getattr(user, "username", None)
    if username:
        return f"{user.id} (@{username})"
    return str(user.id)


def _normalize_reason(reason: str | None) -> str:
    """Handle normalize reason.

    Args:
        reason: Value for reason.

    Returns:
        Return value.
    """
    if not reason:
        return "\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430"
    clean = reason.strip()
    if not clean or clean == "-":
        return "\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430"
    return clean


async def _upsert_restriction(
    session,
    *,
    chat_id: int,
    user_id: int,
    action: str,
    reason: str | None,
    until_date: datetime | None = None,
    created_by: int | None = None,
) -> tuple[ModerationRestriction, bool]:
    """Handle upsert restriction.

    Args:
        session: Value for session.
        chat_id: Value for chat_id.
        user_id: Value for user_id.
        action: Value for action.
        reason: Value for reason.
        until_date: Value for until_date.
        created_by: Value for created_by.

    Returns:
        Return value.
    """
    # Keep a single active restriction per chat/user/action for clean reporting.
    result = await session.execute(
        select(ModerationRestriction).where(
            ModerationRestriction.chat_id == chat_id,
            ModerationRestriction.user_id == user_id,
            ModerationRestriction.action == action,
            ModerationRestriction.active.is_(True),
        )
    )
    record = result.scalar_one_or_none()
    normalized = _normalize_reason(reason)
    if record:
        record.reason = normalized
        record.until_date = until_date
        if created_by:
            record.created_by = created_by
        await session.commit()
        return record, False

    record = ModerationRestriction(
        chat_id=chat_id,
        user_id=user_id,
        action=action,
        reason=normalized,
        until_date=until_date,
        created_by=created_by,
        active=True,
    )
    session.add(record)
    await session.commit()
    return record, True


async def _deactivate_restriction(
    session,
    *,
    chat_id: int,
    user_id: int,
    action: str,
) -> bool:
    """Handle deactivate restriction.

    Args:
        session: Value for session.
        chat_id: Value for chat_id.
        user_id: Value for user_id.
        action: Value for action.

    Returns:
        Return value.
    """
    result = await session.execute(
        select(ModerationRestriction).where(
            ModerationRestriction.chat_id == chat_id,
            ModerationRestriction.user_id == user_id,
            ModerationRestriction.action == action,
            ModerationRestriction.active.is_(True),
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return False
    record.active = False
    await session.commit()
    return True


async def _upsert_moderation_chat(
    sessionmaker: async_sessionmaker,
    chat_id: int,
    title: str | None,
    *,
    active: bool,
) -> None:
    """Handle upsert moderation chat.

    Args:
        sessionmaker: Value for sessionmaker.
        chat_id: Value for chat_id.
        title: Value for title.
        active: Value for active.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat).where(ModerationChat.chat_id == chat_id)
        )
        record = result.scalar_one_or_none()
        if record:
            record.active = active
            record.title = title
        else:
            session.add(ModerationChat(chat_id=chat_id, title=title, active=active))
        await session.commit()


async def _log_member_event(
    sessionmaker: async_sessionmaker,
    *,
    chat_id: int,
    user_id: int,
    event_type: str,
) -> None:
    """Handle log member event.

    Args:
        sessionmaker: Value for sessionmaker.
        chat_id: Value for chat_id.
        user_id: Value for user_id.
        event_type: Value for event_type.
    """
    async with sessionmaker() as session:
        session.add(
            ModerationMemberEvent(
                chat_id=chat_id,
                user_id=user_id,
                event_type=event_type,
            )
        )
        await session.commit()


@router.message(F.text.startswith("/ban"))
async def cmd_ban(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle cmd ban.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not await _is_moderated_chat(sessionmaker, message.chat.id):
        return
    if not message.from_user or not await _is_staff(sessionmaker, message.from_user.id):
        return

    parts = (message.text or "").split()
    target_id, reason = _parse_target_and_reason(message, parts[1:])
    if not target_id:
        await message.answer(
            "Формат: /ban <user_id> [причина] или ответом на сообщение."
        )
        return
    try:
        await message.bot.ban_chat_member(message.chat.id, target_id)
    except Exception:
        await message.answer("Не удалось забанить пользователя.")
        return
    async with sessionmaker() as session:
        record, created = await _upsert_restriction(
            session,
            chat_id=message.chat.id,
            user_id=target_id,
            action="ban",
            reason=reason,
            created_by=message.from_user.id,
        )
        if created:
            await apply_trust_event(
                session,
                target_id,
                "chat_ban",
                -20,
                record.reason or "Бан в чате",
                ref_type="restriction",
                ref_id=record.id,
            )
    reason_text = record.reason or _normalize_reason(reason)
    chat_title = message.chat.title or "-"
    mod_label = _format_tg_user(message.from_user)
    target_label = (
        _format_tg_user(message.reply_to_message.from_user)
        if message.reply_to_message
        else str(target_id)
    )
    log_text = (
        "\u26d4 \u041c\u043e\u0434\u0435\u0440\u0430\u0446\u0438\u044f\n"
        "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435: \u0431\u0430\u043d\n"
        f"\u0427\u0430\u0442: {chat_title} ({message.chat.id})\n"
        f"\u041c\u043e\u0434\u0435\u0440\u0430\u0442\u043e\u0440: {mod_label}\n"
        f"\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c: {target_label}\n"
        f"\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason_text}"
    )
    await _log_info(message.bot, settings, log_text)
    try:
        await message.bot.send_message(
            target_id,
            f"\u0412\u044b \u0437\u0430\u0431\u0430\u043d\u0435\u043d\u044b \u0432 \u0447\u0430\u0442\u0435 {chat_title}. \u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason_text}",
        )
    except Exception:
        pass
    await message.answer(f"Пользователь забанен. Причина: {reason_text}")


@router.message(F.text.startswith("/unban"))
async def cmd_unban(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle cmd unban.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not await _is_moderated_chat(sessionmaker, message.chat.id):
        return
    if not message.from_user or not await _is_staff(sessionmaker, message.from_user.id):
        return

    parts = (message.text or "").split()
    target_id, _ = _parse_target_and_reason(message, parts[1:])
    if not target_id:
        await message.answer("Формат: /unban <user_id> или ответом на сообщение.")
        return
    try:
        await message.bot.unban_chat_member(message.chat.id, target_id)
    except Exception:
        await message.answer("Не удалось разбанить пользователя.")
        return
    async with sessionmaker() as session:
        await _deactivate_restriction(
            session,
            chat_id=message.chat.id,
            user_id=target_id,
            action="ban",
        )
    await message.answer("Пользователь разбанен.")


@router.message(F.text.startswith("/mute"))
async def cmd_mute(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle cmd mute.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not await _is_moderated_chat(sessionmaker, message.chat.id):
        return
    if not message.from_user or not await _is_staff(sessionmaker, message.from_user.id):
        return

    parts = (message.text or "").split()
    if message.reply_to_message and message.reply_to_message.from_user:
        duration_token = parts[1] if len(parts) > 1 else ""
        reason = " ".join(parts[2:]).strip() if len(parts) > 2 else "-"
        target_id = message.reply_to_message.from_user.id
    else:
        if len(parts) < 3:
            await message.answer(
                "Формат: /mute <user_id> <1h/2d> [причина] или ответом на сообщение."
            )
            return
        target_raw = parts[1].lstrip("@")
        if not target_raw.isdigit():
            await message.answer("Укажите корректный user_id.")
            return
        target_id = int(target_raw)
        duration_token = parts[2]
        reason = " ".join(parts[3:]).strip() if len(parts) > 3 else "-"

    duration = _parse_duration(duration_token)
    if not duration:
        await message.answer("Укажите длительность в часах или днях: 1h, 6h, 2d.")
        return
    until_date = datetime.now(timezone.utc) + duration
    permissions = ChatPermissions(can_send_messages=False)
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            target_id,
            permissions=permissions,
            until_date=until_date,
        )
    except Exception:
        await message.answer("Не удалось замутить пользователя.")
        return
    async with sessionmaker() as session:
        record, created = await _upsert_restriction(
            session,
            chat_id=message.chat.id,
            user_id=target_id,
            action="mute",
            reason=reason,
            until_date=until_date,
            created_by=message.from_user.id,
        )
        if created:
            await apply_trust_event(
                session,
                target_id,
                "chat_mute",
                -5,
                record.reason or "Мут в чате",
                ref_type="restriction",
                ref_id=record.id,
            )
    reason_text = record.reason or _normalize_reason(reason)
    chat_title = message.chat.title or "-"
    mod_label = _format_tg_user(message.from_user)
    target_label = (
        _format_tg_user(message.reply_to_message.from_user)
        if message.reply_to_message
        else str(target_id)
    )
    duration_label = duration_token if duration_token else "-"
    until_label = until_date.strftime("%Y-%m-%d %H:%M UTC")
    log_text = (
        "\u26d4 \u041c\u043e\u0434\u0435\u0440\u0430\u0446\u0438\u044f\n"
        "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435: \u043c\u0443\u0442\n"
        f"\u0427\u0430\u0442: {chat_title} ({message.chat.id})\n"
        f"\u041c\u043e\u0434\u0435\u0440\u0430\u0442\u043e\u0440: {mod_label}\n"
        f"\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c: {target_label}\n"
        f"\u0421\u0440\u043e\u043a: {duration_label} (\u0434\u043e {until_label})\n"
        f"\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason_text}"
    )
    await _log_info(message.bot, settings, log_text)
    try:
        await message.bot.send_message(
            target_id,
            (
                f"\u0412\u0430\u043c \u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u043e \u043f\u0438\u0441\u0430\u0442\u044c \u0432 \u0447\u0430\u0442\u0435 {chat_title} "
                f"\u0434\u043e {until_label}. \u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason_text}"
            ),
        )
    except Exception:
        pass
    await message.answer(f"Пользователь в муте. Причина: {reason_text}")


@router.message(F.text.startswith("/unmute"))
async def cmd_unmute(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle cmd unmute.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not await _is_moderated_chat(sessionmaker, message.chat.id):
        return
    if not message.from_user or not await _is_staff(sessionmaker, message.from_user.id):
        return

    parts = (message.text or "").split()
    target_id, _ = _parse_target_and_reason(message, parts[1:])
    if not target_id:
        await message.answer("Формат: /unmute <user_id> или ответом на сообщение.")
        return
    permissions = ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            target_id,
            permissions=permissions,
            until_date=None,
        )
    except Exception:
        await message.answer("Не удалось размутить пользователя.")
        return
    async with sessionmaker() as session:
        await _deactivate_restriction(
            session,
            chat_id=message.chat.id,
            user_id=target_id,
            action="mute",
        )
    await message.answer("Пользователь размучен.")


@router.my_chat_member()
async def on_bot_added(
    event: ChatMemberUpdated, sessionmaker: async_sessionmaker
) -> None:
    """Handle on bot added.

    Args:
        event: Value for event.
        sessionmaker: Value for sessionmaker.
    """
    if event.chat.type not in {"group", "supergroup", "channel"}:
        return
    new_status = event.new_chat_member.status
    if new_status in {"member", "administrator"}:
        await _upsert_moderation_chat(
            sessionmaker,
            event.chat.id,
            event.chat.title,
            active=True,
        )
    elif new_status in {"left", "kicked"}:
        await _upsert_moderation_chat(
            sessionmaker,
            event.chat.id,
            event.chat.title,
            active=False,
        )


@router.chat_member()
async def on_user_join(
    event: ChatMemberUpdated,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle on user join.

    Args:
        event: Value for event.
        sessionmaker: Value for sessionmaker.
    """
    if event.chat.type not in {"group", "supergroup"}:
        return
    if not await _is_moderated_chat(sessionmaker, event.chat.id):
        return
    if event.new_chat_member.user.is_bot:
        return
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    if old_status in {"left", "kicked"} and new_status in {
        "member",
        "administrator",
    }:
        await _log_member_event(
            sessionmaker,
            chat_id=event.chat.id,
            user_id=event.new_chat_member.user.id,
            event_type="join",
        )
        name = html.escape(event.new_chat_member.user.first_name or "друг")
        text = CHAT_WELCOME_TEXT.format(name=name)
        await event.bot.send_message(
            event.chat.id,
            text,
            reply_markup=referral_kb(),
        )
    elif old_status in {"member", "administrator"} and new_status in {
        "left",
        "kicked",
    }:
        await _log_member_event(
            sessionmaker,
            chat_id=event.chat.id,
            user_id=event.new_chat_member.user.id,
            event_type="leave",
        )


async def _is_staff(sessionmaker: async_sessionmaker, user_id: int) -> bool:
    """Handle is staff.

    Args:
        sessionmaker: Value for sessionmaker.
        user_id: Value for user_id.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return False
        return user.role in {"owner", "admin", "moderator", "guarantor"}


async def _is_moderated_chat(sessionmaker: async_sessionmaker, chat_id: int) -> bool:
    """Handle is moderated chat.

    Args:
        sessionmaker: Value for sessionmaker.
        chat_id: Value for chat_id.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat).where(
                ModerationChat.chat_id == chat_id,
                ModerationChat.active.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None


async def _load_blacklist(
    sessionmaker: async_sessionmaker, settings: Settings
) -> list[str]:
    """Handle load blacklist.

    Args:
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.

    Returns:
        Return value.
    """
    words = {w.strip().lower() for w in settings.moderation_blacklist if w.strip()}
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationWord.word).where(ModerationWord.active.is_(True))
        )
        for row in result.all():
            if row[0]:
                words.add(row[0].strip().lower())
    return list(words)


async def _is_banned_sticker_pack(
    sessionmaker: async_sessionmaker, set_name: str
) -> bool:
    """Handle is banned sticker pack.

    Args:
        sessionmaker: Value for sessionmaker.
        set_name: Value for set_name.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationStickerPack).where(
                ModerationStickerPack.set_name == set_name,
                ModerationStickerPack.active.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None


async def _get_sticker_pack_record(
    sessionmaker: async_sessionmaker, set_name: str
) -> ModerationStickerPack | None:
    """Handle get sticker pack record.

    Args:
        sessionmaker: Value for sessionmaker.
        set_name: Value for set_name.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationStickerPack).where(
                ModerationStickerPack.set_name == set_name
            )
        )
        return result.scalar_one_or_none()


async def _get_custom_emoji_records(
    sessionmaker: async_sessionmaker, emoji_ids: list[str]
) -> dict[str, ModerationCustomEmoji]:
    """Handle get custom emoji records.

    Args:
        sessionmaker: Value for sessionmaker.
        emoji_ids: Value for emoji_ids.

    Returns:
        Return value.
    """
    if not emoji_ids:
        return {}
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationCustomEmoji).where(
                ModerationCustomEmoji.emoji_id.in_(emoji_ids)
            )
        )
        records = result.scalars().all()
        return {record.emoji_id: record for record in records}


async def _get_custom_emoji_pack_records(
    sessionmaker: async_sessionmaker, set_names: list[str]
) -> dict[str, ModerationCustomEmojiPack]:
    """Handle get custom emoji pack records.

    Args:
        sessionmaker: Value for sessionmaker.
        set_names: Value for set_names.

    Returns:
        Return value.
    """
    if not set_names:
        return {}
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationCustomEmojiPack).where(
                ModerationCustomEmojiPack.set_name.in_(set_names)
            )
        )
        records = result.scalars().all()
        return {record.set_name: record for record in records}


async def _create_case(
    sessionmaker: async_sessionmaker,
    *,
    kind: str,
    chat_id: int,
    user_id: int | None,
    payload: str | None,
    prev_role: str | None = None,
) -> int:
    """Handle create case.

    Args:
        sessionmaker: Value for sessionmaker.
        kind: Value for kind.
        chat_id: Value for chat_id.
        user_id: Value for user_id.
        payload: Value for payload.
        prev_role: Value for prev_role.

    Returns:
        Return value.
    """
    async with sessionmaker() as session:
        case = ModerationCase(
            kind=kind,
            chat_id=chat_id,
            user_id=user_id,
            payload=payload,
            prev_role=prev_role,
        )
        session.add(case)
        await session.commit()
        return case.id


def _case_kb(case_id: int, kind: str) -> InlineKeyboardMarkup:
    """Handle case kb.

    Args:
        case_id: Value for case_id.
        kind: Value for kind.

    Returns:
        Return value.
    """
    if kind == "word":
        ok_text = "Оставить бан"
        cancel_text = "Снять бан"
    elif kind == "sticker_pack":
        ok_text = "Запретить стикерпак"
        cancel_text = "Разрешить"
    elif kind == "custom_emoji":
        ok_text = "Запретить пак"
        cancel_text = "Разрешить пак"
    else:
        ok_text = "Подтвердить"
        cancel_text = "Отклонить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ok_text,
                    callback_data=f"mod_case_ok:{case_id}",
                ),
                InlineKeyboardButton(
                    text=cancel_text,
                    callback_data=f"mod_case_cancel:{case_id}",
                ),
            ]
        ]
    )


async def _log_case(
    bot,
    settings: Settings,
    text: str,
    case_id: int,
    kind: str,
) -> None:
    """Handle log case.

    Args:
        bot: Value for bot.
        settings: Value for settings.
        text: Value for text.
        case_id: Value for case_id.
        kind: Value for kind.
    """
    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        return
    await _safe_send_message(
        bot,
        chat_id,
        text,
        message_thread_id=topic_id,
        reply_markup=_case_kb(case_id, kind),
    )


async def _log_info(
    bot,
    settings: Settings,
    text: str,
) -> None:
    """Handle log info.

    Args:
        bot: Value for bot.
        settings: Value for settings.
        text: Value for text.
    """
    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        return
    await _safe_send_message(
        bot,
        chat_id,
        text,
        message_thread_id=topic_id,
    )


async def _safe_send_message(bot, chat_id: int, text: str, **kwargs) -> None:
    """Handle safe send message.

    Args:
        bot: Value for bot.
        chat_id: Value for chat_id.
        text: Value for text.
        **kwargs: Value for **kwargs.
    """
    try:
        await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        await bot.send_message(chat_id, text, **kwargs)


def _parse_payload(payload: str | None) -> dict[str, object]:
    """Handle parse payload.

    Args:
        payload: Value for payload.

    Returns:
        Return value.
    """
    if not payload:
        return {}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()}
    return {}


async def _forward_to_admin(
    bot,
    settings: Settings,
    message: Message,
) -> None:
    """Handle forward to admin.

    Args:
        bot: Value for bot.
        settings: Value for settings.
        message: Value for message.
    """
    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        return
    try:
        await bot.forward_message(
            chat_id=chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=topic_id,
        )
    except Exception:
        pass


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def moderate_chat(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle moderate chat.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if not message.from_user or message.from_user.is_bot:
        return

    if not await _is_moderated_chat(sessionmaker, message.chat.id):
        return

    if await _is_staff(sessionmaker, message.from_user.id):
        return

    content = message.text or message.caption or ""
    if content:
        blacklist_words = await _load_blacklist(sessionmaker, settings)
        blacklist_hit = contains_blacklist(content, blacklist_words)
    else:
        blacklist_hit = False
    if blacklist_hit:
        try:
            await message.delete()
        except Exception:
            pass

        async with sessionmaker() as session:
            result = await session.execute(
                select(User).where(User.id == message.from_user.id)
            )
            db_user = result.scalar_one_or_none()
            prev_role = db_user.role if db_user else None
            record, created = await _upsert_restriction(
                session,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                action="ban",
                reason="\u0410\u0432\u0442\u043e\u0431\u0430\u043d: \u0437\u0430\u043f\u0440\u0435\u0449\u0435\u043d\u043d\u044b\u0435 \u0441\u043b\u043e\u0432\u0430",
            )
            if created:
                await apply_trust_event(
                    session,
                    message.from_user.id,
                    "chat_ban",
                    -20,
                    record.reason
                    or "\u0411\u0430\u043d \u0432 \u0447\u0430\u0442\u0435",
                    ref_type="restriction",
                    ref_id=record.id,
                )
        reason_text = record.reason or _normalize_reason(None)

        try:
            await message.bot.ban_chat_member(message.chat.id, message.from_user.id)
        except Exception:
            pass

        try:
            await message.bot.send_message(
                message.from_user.id,
                (
                    "\u0412\u0430\u0448\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u043d\u0430\u0440\u0443\u0448\u0430\u0435\u0442 \u043f\u0440\u0430\u0432\u0438\u043b\u0430. "
                    "\u0412\u044b \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u044b \u0432 \u0447\u0430\u0442\u0435. "
                    f"\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason_text}"
                ),
            )
        except Exception:
            pass

        case_id = await _create_case(
            sessionmaker,
            kind="word",
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            payload=content[:500],
            prev_role=prev_role,
        )
        await _log_case(
            message.bot,
            settings,
            (
                "🚫 <b>Автоблокировка по триггеру</b>\n"
                f"Чат: {message.chat.title or '-'} ({message.chat.id})\n"
                f"Пользователь: {message.from_user.id}\n"
                f"Фрагмент: {content[:300]}\n"
                "Действие: оставьте бан или снимите его кнопкой ниже."
            ),
            case_id,
            "word",
        )
        return

    if message.sticker:
        set_name = message.sticker.set_name
        set_title = None
        if set_name:
            try:
                sticker_set = await message.bot.get_sticker_set(set_name)
                set_title = sticker_set.title
            except Exception:
                pass
        if set_name:
            record = await _get_sticker_pack_record(sessionmaker, set_name)
            if record:
                if record.active:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                return

        await _forward_to_admin(message.bot, settings, message)
        try:
            await message.bot.send_message(
                message.from_user.id,
                "Стикер отправлен на модерацию.",
            )
        except Exception:
            pass
        payload = json.dumps(
            {
                "set_name": set_name or "",
                "set_title": set_title or "",
                "message_id": message.message_id,
            },
            ensure_ascii=False,
        )
        case_id = await _create_case(
            sessionmaker,
            kind="sticker_pack",
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            payload=payload,
        )
        await _log_case(
            message.bot,
            settings,
            (
                "🧩 <b>Стикер на модерацию</b>\n"
                f"Чат: {message.chat.title or '-'} ({message.chat.id})\n"
                f"Пользователь: {message.from_user.id}\n"
                f"Пак: {set_title or set_name or 'нет набора'}\n"
                f"ID пака: {set_name or '-'}\n"
                "Действие: запретить или разрешить кнопками ниже."
            ),
            case_id,
            "sticker_pack",
        )
        return

    entities = (message.entities or []) + (message.caption_entities or [])
    emoji_ids = []
    for entity in entities:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            emoji_ids.append(entity.custom_emoji_id)
    if emoji_ids:
        emoji_ids = list(dict.fromkeys(emoji_ids))
        pack_map = {}
        pack_titles = {}
        unknown_emoji_ids = []
        try:
            stickers = await message.bot.get_custom_emoji_stickers(emoji_ids)
        except Exception:
            stickers = []

        if stickers:
            sticker_map = {
                sticker.custom_emoji_id: sticker
                for sticker in stickers
                if sticker.custom_emoji_id
            }
            for emoji_id in emoji_ids:
                sticker = sticker_map.get(emoji_id)
                set_name = getattr(sticker, "set_name", None) if sticker else None
                if set_name:
                    pack_map.setdefault(set_name, []).append(emoji_id)
                    if set_name not in pack_titles:
                        pack_titles[set_name] = getattr(
                            sticker, "set_title", None
                        ) or getattr(sticker, "title", None)
                else:
                    unknown_emoji_ids.append(emoji_id)
        else:
            unknown_emoji_ids = emoji_ids

        pack_records = {}
        unknown_packs = []
        if pack_map:
            pack_records = await _get_custom_emoji_pack_records(
                sessionmaker, list(pack_map.keys())
            )
            if any(record.active for record in pack_records.values()):
                try:
                    await message.delete()
                except Exception:
                    pass
                return
            unknown_packs = [
                name for name in pack_map.keys() if name not in pack_records
            ]

        emoji_records = {}
        unknown_emojis = []
        if unknown_emoji_ids:
            emoji_records = await _get_custom_emoji_records(
                sessionmaker, unknown_emoji_ids
            )
            if any(record.active for record in emoji_records.values()):
                try:
                    await message.delete()
                except Exception:
                    pass
                return
            unknown_emojis = [
                emoji_id
                for emoji_id in unknown_emoji_ids
                if emoji_id not in emoji_records
            ]

        if not unknown_packs and not unknown_emojis:
            return

        await _forward_to_admin(message.bot, settings, message)
        try:
            await message.bot.send_message(
                message.from_user.id,
                "?????? ?????????? ?? ?????????.",
            )
        except Exception:
            pass

        for set_name in unknown_packs:
            payload = json.dumps(
                {
                    "set_name": set_name,
                    "set_title": pack_titles.get(set_name) or "",
                    "emoji_ids": pack_map.get(set_name, []),
                    "message_id": message.message_id,
                },
                ensure_ascii=False,
            )
            case_id = await _create_case(
                sessionmaker,
                kind="custom_emoji",
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                payload=payload,
            )
            pack_title = pack_titles.get(set_name) or "-"
            await _log_case(
                message.bot,
                settings,
                (
                    "🧩 <b>Пак кастомных эмодзи на модерацию</b>\n"
                    f"Чат: {message.chat.title or '-'} ({message.chat.id})\n"
                    f"Пользователь: {message.from_user.id}\n"
                    f"Пак: {pack_title}\n"
                    f"ID пака: {set_name}\n"
                    "Действие: запретить или разрешить кнопками ниже."
                ),
                case_id,
                "custom_emoji",
            )

        if unknown_emojis:
            payload = json.dumps(
                {
                    "emoji_ids": unknown_emojis,
                    "message_id": message.message_id,
                },
                ensure_ascii=False,
            )
            case_id = await _create_case(
                sessionmaker,
                kind="custom_emoji",
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                payload=payload,
            )
            list_preview = ", ".join(unknown_emojis[:10])
            if len(unknown_emojis) > 10:
                list_preview = f"{list_preview} ..."
            await _log_case(
                message.bot,
                settings,
                (
                    "🧩 <b>Кастомные эмодзи на модерацию</b>\n"
                    f"Чат: {message.chat.title or '-'} ({message.chat.id})\n"
                    f"Пользователь: {message.from_user.id}\n"
                    f"ID эмодзи (всего {len(unknown_emojis)}): {list_preview}\n"
                    "Действие: запретить или разрешить кнопками ниже."
                ),
                case_id,
                "custom_emoji",
            )
        return

        return


@router.callback_query(F.data.startswith("mod_case_ok:"))
async def mod_case_ok(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle mod case ok.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    if not await _is_staff(sessionmaker, callback.from_user.id):
        await callback.answer("Нет прав доступа.")
        return
    case_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationCase).where(ModerationCase.id == case_id)
        )
        case = result.scalar_one_or_none()
        if not case or case.status != "pending":
            await callback.answer("Заявка уже обработана.")
            return
        case.status = "approved"

        payload = _parse_payload(case.payload)
        message_id = payload.get("message_id")
        if message_id and str(message_id).isdigit():
            try:
                await callback.bot.delete_message(
                    chat_id=case.chat_id,
                    message_id=int(message_id),
                )
            except Exception:
                pass

        if case.kind == "sticker_pack":
            set_name = payload.get("set_name") or case.payload
            if set_name:
                result = await session.execute(
                    select(ModerationStickerPack).where(
                        ModerationStickerPack.set_name == set_name
                    )
                )
                record = result.scalar_one_or_none()
                if record:
                    record.active = True
                else:
                    session.add(ModerationStickerPack(set_name=set_name, active=True))
        if case.kind == "custom_emoji":
            set_name = payload.get("set_name")
            if isinstance(set_name, str) and set_name:
                result = await session.execute(
                    select(ModerationCustomEmojiPack).where(
                        ModerationCustomEmojiPack.set_name == set_name
                    )
                )
                record = result.scalar_one_or_none()
                title = payload.get("set_title")
                if record:
                    record.active = False
                    if isinstance(title, str) and title:
                        record.title = title
                else:
                    session.add(
                        ModerationCustomEmojiPack(
                            set_name=set_name,
                            title=title if isinstance(title, str) else None,
                            active=False,
                        )
                    )
            else:
                emoji_ids = payload.get("emoji_ids") or []
                if isinstance(emoji_ids, str):
                    emoji_ids = [emoji_ids]
                if not emoji_ids:
                    emoji_id = payload.get("emoji_id") or case.payload
                    if emoji_id:
                        emoji_ids = [emoji_id]
                for emoji_id in emoji_ids:
                    result = await session.execute(
                        select(ModerationCustomEmoji).where(
                            ModerationCustomEmoji.emoji_id == emoji_id
                        )
                    )
                    record = result.scalar_one_or_none()
                    if record:
                        record.active = False
                    else:
                        session.add(
                            ModerationCustomEmoji(emoji_id=emoji_id, active=False)
                        )
        await session.commit()
    status_text = "✅ Забанено"
    if case.kind == "word":
        status_text = "✅ Бан подтвержден"
    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n{status_text}",
            reply_markup=None,
            parse_mode="HTML",
        )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.answer("Подтверждено.")


@router.callback_query(F.data.startswith("mod_case_cancel:"))
async def mod_case_cancel(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle mod case cancel.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    if not await _is_staff(sessionmaker, callback.from_user.id):
        await callback.answer("Нет прав доступа.")
        return
    case_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationCase).where(ModerationCase.id == case_id)
        )
        case = result.scalar_one_or_none()
        if not case or case.status != "pending":
            await callback.answer("Заявка уже обработана.")
            return
        case.status = "rejected"
        if case.kind == "word" and case.user_id:
            result = await session.execute(select(User).where(User.id == case.user_id))
            user = result.scalar_one_or_none()
            if user:
                user.role = case.prev_role or "user"
        await session.commit()
    try:
        if case and case.kind == "word" and case.user_id:
            await callback.bot.unban_chat_member(case.chat_id, case.user_id)
    except Exception:
        pass
    status_text = "✅ Разрешено"
    if case and case.kind == "word":
        status_text = "✅ Бан отменен"
    if case and case.kind in {"sticker_pack", "custom_emoji"}:
        payload = _parse_payload(case.payload)
        if case.kind == "sticker_pack":
            set_name = payload.get("set_name") or case.payload
            if set_name:
                async with sessionmaker() as session:
                    result = await session.execute(
                        select(ModerationStickerPack).where(
                            ModerationStickerPack.set_name == set_name
                        )
                    )
                    record = result.scalar_one_or_none()
                    if record:
                        record.active = False
                    else:
                        session.add(
                            ModerationStickerPack(set_name=set_name, active=False)
                        )
                    await session.commit()
        if case.kind == "custom_emoji":
            handled_pack = False
            set_name = payload.get("set_name")
            if isinstance(set_name, str) and set_name:
                async with sessionmaker() as session:
                    result = await session.execute(
                        select(ModerationCustomEmojiPack).where(
                            ModerationCustomEmojiPack.set_name == set_name
                        )
                    )
                    record = result.scalar_one_or_none()
                    title = payload.get("set_title")
                    if record:
                        record.active = False
                        if isinstance(title, str) and title:
                            record.title = title
                    else:
                        session.add(
                            ModerationCustomEmojiPack(
                                set_name=set_name,
                                title=title if isinstance(title, str) else None,
                                active=False,
                            )
                        )
                    await session.commit()
                handled_pack = True
            emoji_ids = payload.get("emoji_ids") or []
            if isinstance(emoji_ids, str):
                emoji_ids = [emoji_ids]
            if not emoji_ids and not handled_pack:
                emoji_id = payload.get("emoji_id") or case.payload
                if emoji_id:
                    emoji_ids = [emoji_id]
            if emoji_ids and not handled_pack:
                async with sessionmaker() as session:
                    for emoji_id in emoji_ids:
                        result = await session.execute(
                            select(ModerationCustomEmoji).where(
                                ModerationCustomEmoji.emoji_id == emoji_id
                            )
                        )
                        record = result.scalar_one_or_none()
                        if record:
                            record.active = False
                        else:
                            session.add(
                                ModerationCustomEmoji(emoji_id=emoji_id, active=False)
                            )
                    await session.commit()
    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n{status_text}",
            reply_markup=None,
            parse_mode="HTML",
        )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    answer_text = "Разрешено."
    if case and case.kind == "word":
        answer_text = "Бан отменен."
    await callback.answer(answer_text)
