# -*- coding: utf-8 -*-
"""Модуль модерации чатов."""

from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
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

from bot.config import Settings, load_settings
from bot.db.models import (
    ModerationCase,
    ModerationChat,
    ModerationCustomEmoji,
    ModerationCustomEmojiPack,
    ModerationMemberEvent,
    ModerationRestriction,
    ModerationStickerPack,
    ModerationWarn,
    ModerationWord,
    DealRoom,
    User,
)
from bot.keyboards.common import referral_kb
from bot.utils.admin_target import get_admin_target
from bot.utils.moderation import contains_blacklist
from bot.utils.roles import is_owner, is_staff as is_staff_role
from bot.utils.texts import CHAT_WELCOME_TEXT
from bot.services.trust import apply_trust_event

router = Router()

MODERATION_LOGS_ENABLED = False


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _looks_like_username(value: str) -> bool:
    return bool(_USERNAME_RE.fullmatch(value))


def _parse_target_and_reason(
    message: Message, args: list[str]
) -> tuple[int | str | None, str]:
    """Обрабатывает parse target and reason.

    Аргументы:
        message: Значение message.
        args: Значение args.

    Возвращает:
        Возвращает Значение.
    """
    if args:
        raw_id = args[0].strip()
        reason = " ".join(args[1:]).strip() if len(args) > 1 else "-"
        if raw_id:
            stripped = raw_id.lstrip("@")
            if stripped.isdigit():
                return int(stripped), reason or "-"
            if raw_id.startswith("@") and len(raw_id) > 1:
                return raw_id, reason or "-"
            if not message.reply_to_message and _looks_like_username(raw_id):
                return raw_id, reason or "-"
    if message.reply_to_message and message.reply_to_message.forward_from:
        reason = " ".join(args).strip() if args else "-"
        return message.reply_to_message.forward_from.id, reason or "-"
    if not args:
        return None, ""
    return None, ""


def _parse_duration(value: str) -> timedelta | None:
    """Обрабатывает parse duration.

    Аргументы:
        value: Значение value.

    Возвращает:
        Возвращает Значение.
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
    """Обрабатывает format tg user.

    Аргументы:
        user: Значение user.

    Возвращает:
        Возвращает Значение.
    """
    if not user:
        return "-"
    username = getattr(user, "username", None)
    if username:
        return f"{user.id} (@{username})"
    return str(user.id)


def _normalize_reason(reason: str | None) -> str:
    """Обрабатывает normalize reason.

    Аргументы:
        reason: Значение reason.

    Возвращает:
        Возвращает Значение.
    """
    if not reason:
        return "Причина не указана"
    clean = reason.strip()
    if not clean or clean == "-":
        return "Причина не указана"
    return clean


async def _resolve_user_identifier(bot, identifier: int | str | None) -> int | None:
    if identifier is None:
        return None
    if isinstance(identifier, int):
        return identifier
    username = identifier.lstrip("@")
    if not username:
        return None
    try:
        user = await bot.get_chat(f"@{username}")
    except TelegramBadRequest:
        return None
    return user.id


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
    """Обрабатывает upsert restriction.

    Аргументы:
        session: Значение session.
        chat_id: Значение chat_id.
        user_id: Значение user_id.
        action: Значение action.
        reason: Значение reason.
        until_date: Значение until_date.
        created_by: Значение created_by.

    Возвращает:
        Возвращает Значение.
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
    """Обрабатывает deactivate restriction.

    Аргументы:
        session: Значение session.
        chat_id: Значение chat_id.
        user_id: Значение user_id.
        action: Значение action.

    Возвращает:
        Возвращает Значение.
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
    """Обрабатывает upsert moderation chat.

    Аргументы:
        sessionmaker: Значение sessionmaker.
        chat_id: Значение chat_id.
        title: Значение title.
        active: Значение active.
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
    """Обрабатывает log member event.

    Аргументы:
        sessionmaker: Значение sessionmaker.
        chat_id: Значение chat_id.
        user_id: Значение user_id.
        event_type: Значение event_type.
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


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    return load_settings()


async def _is_staff(sessionmaker: async_sessionmaker, user_id: int) -> bool:
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if not user:
        return False
    role = user.role or "user"
    settings = _cached_settings()
    return is_staff_role(role) or is_owner(role, settings.owner_ids, user.id)


async def _is_moderated_chat(
    sessionmaker: async_sessionmaker, chat_id: int
) -> bool:
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat.id).where(
                ModerationChat.chat_id == chat_id,
                ModerationChat.active.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None


async def _get_moderated_chat_ids(
    sessionmaker: async_sessionmaker,
) -> list[int]:
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat.chat_id)
            .where(ModerationChat.active.is_(True))
            .order_by(ModerationChat.id.asc())
        )
        return [row[0] for row in result.all() if row and row[0]]


async def _has_moderation_rights(
    bot,
    sessionmaker: async_sessionmaker,
    chat_id: int,
    user_id: int,
) -> bool:
    if await _is_staff(sessionmaker, user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    status = getattr(member, "status", None)
    if status == "creator":
        return True
    if status == "administrator":
        return bool(getattr(member, "can_restrict_members", False))
    return False


async def _log_info(bot, settings: Settings, text: str) -> None:
    if not MODERATION_LOGS_ENABLED:
        return
    chat_id, topic_id = get_admin_target(settings)
    if not chat_id:
        return
    try:
        await bot.send_message(chat_id, text, message_thread_id=topic_id)
    except Exception:
        pass


async def _load_blacklist(
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> list[str]:
    system_words = [word for word in settings.moderation_blacklist if word.strip()]
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationWord.word).where(ModerationWord.active.is_(True))
        )
        custom_words = [row[0] for row in result.all() if row[0]]
    return list(dict.fromkeys(system_words + custom_words))


async def _forward_to_admin(bot, settings: Settings, message: Message) -> None:
    chat_id, topic_id = get_admin_target(settings)
    if not chat_id:
        return
    try:
        await bot.forward_message(
            chat_id,
            message.chat.id,
            message.message_id,
            message_thread_id=topic_id,
        )
    except Exception:
        pass


async def _create_case(
    sessionmaker: async_sessionmaker,
    *,
    kind: str,
    chat_id: int,
    user_id: int | None,
    payload: str | None,
    prev_role: str | None = None,
) -> int:
    async with sessionmaker() as session:
        record = ModerationCase(
            kind=kind,
            chat_id=chat_id,
            user_id=user_id,
            payload=payload,
            prev_role=prev_role,
            status="pending",
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record.id


def _case_actions_kb(case_id: int, kind: str) -> InlineKeyboardMarkup:
    if kind == "word":
        ban_label = "🚫 Оставить бан"
        allow_label = "✅ Снять бан"
    else:
        ban_label = "🚫 Запретить"
        allow_label = "✅ Разрешить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ban_label,
                    callback_data=f"mod_case_ok:{case_id}",
                ),
                InlineKeyboardButton(
                    text=allow_label,
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
    chat_id, topic_id = get_admin_target(settings)
    if not chat_id:
        return
    await bot.send_message(
        chat_id,
        text,
        message_thread_id=topic_id,
        reply_markup=_case_actions_kb(case_id, kind),
        parse_mode="HTML",
    )


async def _get_sticker_pack_record(
    sessionmaker: async_sessionmaker,
    set_name: str,
) -> ModerationStickerPack | None:
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationStickerPack).where(
                ModerationStickerPack.set_name == set_name
            )
        )
        return result.scalar_one_or_none()


async def _get_custom_emoji_pack_records(
    sessionmaker: async_sessionmaker,
    set_names: list[str],
) -> dict[str, ModerationCustomEmojiPack]:
    if not set_names:
        return {}
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationCustomEmojiPack).where(
                ModerationCustomEmojiPack.set_name.in_(set_names)
            )
        )
        records = result.scalars().all()
    return {record.set_name: record for record in records if record.set_name}


async def _get_custom_emoji_records(
    sessionmaker: async_sessionmaker,
    emoji_ids: list[str],
) -> dict[str, ModerationCustomEmoji]:
    if not emoji_ids:
        return {}
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationCustomEmoji).where(
                ModerationCustomEmoji.emoji_id.in_(emoji_ids)
            )
        )
        records = result.scalars().all()
    return {record.emoji_id: record for record in records if record.emoji_id}


def _parse_payload(payload: str | None) -> dict:
    if not payload:
        return {}
    try:
        data = json.loads(payload)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@router.message(F.text == "/mod_chat_add")
async def mod_chat_add(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Добавляет текущий чат в модерацию."""
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not message.from_user or not await _has_moderation_rights(
        message.bot, sessionmaker, message.chat.id, message.from_user.id
    ):
        await message.answer("Нет доступа.")
        return
    await _upsert_moderation_chat(
        sessionmaker,
        message.chat.id,
        message.chat.title,
        active=True,
    )
    await message.answer("Чат добавлен в модерацию.")


@router.message(F.text == "/mod_chat_remove")
async def mod_chat_remove(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Убирает текущий чат из модерации."""
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not message.from_user or not await _is_staff(sessionmaker, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await _upsert_moderation_chat(
        sessionmaker,
        message.chat.id,
        message.chat.title,
        active=False,
    )
    await message.answer("Чат убран из модерации.")


@router.message(F.text.startswith("/ban"))
async def cmd_ban(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Обрабатывает cmd ban.

    Аргументы:
        message: Значение message.
        sessionmaker: Значение sessionmaker.
        settings: Значение settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not message.from_user:
        return

    is_staff = await _is_staff(sessionmaker, message.from_user.id)
    if not is_staff:
        if not await _is_moderated_chat(sessionmaker, message.chat.id):
            await message.answer(
                "Чат не в списке модерации. Используйте /mod_chat_add."
            )
            return
        if not await _has_moderation_rights(
            message.bot, sessionmaker, message.chat.id, message.from_user.id
        ):
            await message.answer("Нет доступа.")
            return

    parts = (message.text or "").split()
    target_identifier, reason = _parse_target_and_reason(message, parts[1:])
    target_id = await _resolve_user_identifier(message.bot, target_identifier)
    if not target_id:
        await message.answer(
            "Формат: /ban user_id/@username [причина] или ответом на пересланное сообщение."
        )
        return

    chat_ids = await _get_moderated_chat_ids(sessionmaker)
    if not chat_ids:
        await message.answer("Нет активных чатов модерации.")
        return
    multi_chat = len(chat_ids) > 1

    success_ids: list[int] = []
    failed_ids: list[int] = []
    for chat_id in chat_ids:
        try:
            await message.bot.ban_chat_member(chat_id, target_id)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await message.bot.ban_chat_member(chat_id, target_id)
            except Exception:
                failed_ids.append(chat_id)
                continue
        except Exception:
            failed_ids.append(chat_id)
            continue
        success_ids.append(chat_id)

    if not success_ids:
        await message.answer("Не удалось забанить пользователя.")
        return

    reason_text = _normalize_reason(reason)
    applied_trust = False
    async with sessionmaker() as session:
        for chat_id in success_ids:
            record, created = await _upsert_restriction(
                session,
                chat_id=chat_id,
                user_id=target_id,
                action="ban",
                reason=reason,
                created_by=message.from_user.id,
            )
            if created and not applied_trust:
                await apply_trust_event(
                    session,
                    target_id,
                    "chat_ban",
                    -20,
                    record.reason or "Бан в чате",
                    ref_type="restriction",
                    ref_id=record.id,
                )
                applied_trust = True

    chat_label = (
        f"Все модерируемые чаты ({len(chat_ids)})"
        if multi_chat
        else f"{message.chat.title or '-'} ({message.chat.id})"
    )
    mod_label = _format_tg_user(message.from_user)
    target_label = (
        _format_tg_user(message.reply_to_message.from_user)
        if message.reply_to_message and message.reply_to_message.from_user
        else str(target_id)
    )
    log_text = (
        "⛔ Модерация\n"
        "Действие: бан\n"
        f"Чаты: {chat_label}\n"
        f"Модератор: {mod_label}\n"
        f"Пользователь: {target_label}\n"
        f"Причина: {reason_text}"
    )
    if failed_ids:
        log_text += f"\nОшибки: {len(failed_ids)}"
    await _log_info(message.bot, settings, log_text)
    try:
        if len(success_ids) > 1:
            dm_text = f"Вы забанены в чатах модерации. Причина: {reason_text}"
        else:
            chat_title = message.chat.title or "-"
            dm_text = f"Вы забанены в чате {chat_title}. Причина: {reason_text}"
        await message.bot.send_message(target_id, dm_text)
    except Exception:
        pass

    summary = f"Пользователь забанен. Успешно: {len(success_ids)}"
    if failed_ids:
        summary += f", ошибки: {len(failed_ids)}."
    await message.answer(summary)


@router.message(F.text.startswith("/unban"))
async def cmd_unban(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Обрабатывает cmd unban.

    Аргументы:
        message: Значение message.
        sessionmaker: Значение sessionmaker.
        settings: Значение settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not message.from_user:
        return

    is_staff = await _is_staff(sessionmaker, message.from_user.id)
    if not is_staff:
        if not await _is_moderated_chat(sessionmaker, message.chat.id):
            await message.answer(
                "Чат не в списке модерации. Используйте /mod_chat_add."
            )
            return
        if not await _has_moderation_rights(
            message.bot, sessionmaker, message.chat.id, message.from_user.id
        ):
            await message.answer("Нет доступа.")
            return

    parts = (message.text or "").split()
    target_identifier, _ = _parse_target_and_reason(message, parts[1:])
    target_id = await _resolve_user_identifier(message.bot, target_identifier)
    if not target_id:
        await message.answer(
            "Формат: /unban user_id/@username или ответом на пересланное сообщение."
        )
        return

    chat_ids = await _get_moderated_chat_ids(sessionmaker)
    if not chat_ids:
        await message.answer("Нет активных чатов модерации.")
        return

    success_ids: list[int] = []
    failed_ids: list[int] = []
    for chat_id in chat_ids:
        try:
            await message.bot.unban_chat_member(chat_id, target_id)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await message.bot.unban_chat_member(chat_id, target_id)
            except Exception:
                failed_ids.append(chat_id)
                continue
        except Exception:
            failed_ids.append(chat_id)
            continue
        success_ids.append(chat_id)

    if not success_ids:
        await message.answer("Не удалось разбанить пользователя.")
        return

    async with sessionmaker() as session:
        for chat_id in success_ids:
            await _deactivate_restriction(
                session,
                chat_id=chat_id,
                user_id=target_id,
                action="ban",
            )

    summary = f"Пользователь разбанен. Успешно: {len(success_ids)}"
    if failed_ids:
        summary += f", ошибки: {len(failed_ids)}."
    await message.answer(summary)


@router.message(F.text.startswith("/mute"))
async def cmd_mute(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Обрабатывает cmd mute.

    Аргументы:
        message: Значение message.
        sessionmaker: Значение sessionmaker.
        settings: Значение settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not message.from_user:
        return

    is_staff = await _is_staff(sessionmaker, message.from_user.id)
    if not is_staff:
        if not await _is_moderated_chat(sessionmaker, message.chat.id):
            await message.answer(
                "Чат не в списке модерации. Используйте /mod_chat_add."
            )
            return
        if not await _has_moderation_rights(
            message.bot, sessionmaker, message.chat.id, message.from_user.id
        ):
            await message.answer("Нет доступа.")
            return

    parts = (message.text or "").split()
    duration_token = ""
    reason = "-"
    target_identifier: int | str | None = None
    if message.reply_to_message and message.reply_to_message.from_user:
        duration_token = parts[1] if len(parts) > 1 else ""
        reason = " ".join(parts[2:]).strip() if len(parts) > 2 else "-"
        if message.reply_to_message.forward_from:
            target_identifier = message.reply_to_message.forward_from.id
        else:
            target_identifier = message.reply_to_message.from_user.id
    else:
        if len(parts) < 3:
            await message.answer(
                "Формат: /mute user_id/@username 1h/2d [причина] или ответом на пересланное сообщение."
            )
            return
        target_identifier = parts[1]
        duration_token = parts[2]
        reason = " ".join(parts[3:]).strip() if len(parts) > 3 else "-"

    target_id = await _resolve_user_identifier(message.bot, target_identifier)
    if not target_id:
        await message.answer(
            "Формат: /mute user_id/@username 1h/2d [причина] или ответом на пересланное сообщение."
        )
        return

    duration = _parse_duration(duration_token)
    if not duration:
        await message.answer("Укажите длительность в часах или днях: 1h, 6h, 2d.")
        return
    until_date = datetime.now(timezone.utc) + duration
    permissions = ChatPermissions(can_send_messages=False)

    chat_ids = await _get_moderated_chat_ids(sessionmaker)
    if not chat_ids:
        await message.answer("Нет активных чатов модерации.")
        return
    multi_chat = len(chat_ids) > 1

    success_ids: list[int] = []
    failed_ids: list[int] = []
    for chat_id in chat_ids:
        try:
            await message.bot.restrict_chat_member(
                chat_id,
                target_id,
                permissions=permissions,
                until_date=until_date,
            )
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await message.bot.restrict_chat_member(
                    chat_id,
                    target_id,
                    permissions=permissions,
                    until_date=until_date,
                )
            except Exception:
                failed_ids.append(chat_id)
                continue
        except Exception:
            failed_ids.append(chat_id)
            continue
        success_ids.append(chat_id)

    if not success_ids:
        await message.answer("Не удалось замутить пользователя.")
        return

    reason_text = _normalize_reason(reason)
    applied_trust = False
    async with sessionmaker() as session:
        for chat_id in success_ids:
            record, created = await _upsert_restriction(
                session,
                chat_id=chat_id,
                user_id=target_id,
                action="mute",
                reason=reason,
                until_date=until_date,
                created_by=message.from_user.id,
            )
            if created and not applied_trust:
                await apply_trust_event(
                    session,
                    target_id,
                    "chat_mute",
                    -5,
                    record.reason or "Мут в чате",
                    ref_type="restriction",
                    ref_id=record.id,
                )
                applied_trust = True

    chat_label = (
        f"Все модерируемые чаты ({len(chat_ids)})"
        if multi_chat
        else f"{message.chat.title or '-'} ({message.chat.id})"
    )
    mod_label = _format_tg_user(message.from_user)
    target_label = (
        _format_tg_user(message.reply_to_message.from_user)
        if message.reply_to_message and message.reply_to_message.from_user
        else str(target_id)
    )
    duration_label = duration_token if duration_token else "-"
    until_label = until_date.strftime("%Y-%m-%d %H:%M UTC")
    log_text = (
        "⛔ Модерация\n"
        "Действие: мут\n"
        f"Чаты: {chat_label}\n"
        f"Модератор: {mod_label}\n"
        f"Пользователь: {target_label}\n"
        f"Срок: {duration_label} (до {until_label})\n"
        f"Причина: {reason_text}"
    )
    if failed_ids:
        log_text += f"\nОшибки: {len(failed_ids)}"
    await _log_info(message.bot, settings, log_text)
    try:
        if len(success_ids) > 1:
            dm_text = (
                f"Вам ограничено писать в чатах модерации до {until_label}. "
                f"Причина: {reason_text}"
            )
        else:
            chat_title = message.chat.title or "-"
            dm_text = (
                f"Вам ограничено писать в чате {chat_title} до {until_label}. "
                f"Причина: {reason_text}"
            )
        await message.bot.send_message(target_id, dm_text)
    except Exception:
        pass

    summary = f"Пользователь в муте. Успешно: {len(success_ids)}"
    if failed_ids:
        summary += f", ошибки: {len(failed_ids)}."
    await message.answer(summary)
@router.message(F.text.startswith("/unmute"))
async def cmd_unmute(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Обрабатывает cmd unmute.

    Аргументы:
        message: Значение message.
        sessionmaker: Значение sessionmaker.
        settings: Значение settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not message.from_user:
        return

    is_staff = await _is_staff(sessionmaker, message.from_user.id)
    if not is_staff:
        if not await _is_moderated_chat(sessionmaker, message.chat.id):
            await message.answer(
                "Чат не в списке модерации. Используйте /mod_chat_add."
            )
            return
        if not await _has_moderation_rights(
            message.bot, sessionmaker, message.chat.id, message.from_user.id
        ):
            await message.answer("Нет доступа.")
            return

    parts = (message.text or "").split()
    target_identifier, _ = _parse_target_and_reason(message, parts[1:])
    target_id = await _resolve_user_identifier(message.bot, target_identifier)
    if not target_id:
        await message.answer(
            "Формат: /unmute user_id/@username или ответом на пересланное сообщение."
        )
        return

    permissions = ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )

    chat_ids = await _get_moderated_chat_ids(sessionmaker)
    if not chat_ids:
        await message.answer("Нет активных чатов модерации.")
        return

    success_ids: list[int] = []
    failed_ids: list[int] = []
    for chat_id in chat_ids:
        try:
            await message.bot.restrict_chat_member(
                chat_id,
                target_id,
                permissions=permissions,
                until_date=None,
            )
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await message.bot.restrict_chat_member(
                    chat_id,
                    target_id,
                    permissions=permissions,
                    until_date=None,
                )
            except Exception:
                failed_ids.append(chat_id)
                continue
        except Exception:
            failed_ids.append(chat_id)
            continue
        success_ids.append(chat_id)

    if not success_ids:
        await message.answer("Не удалось размутить пользователя.")
        return

    async with sessionmaker() as session:
        for chat_id in success_ids:
            await _deactivate_restriction(
                session,
                chat_id=chat_id,
                user_id=target_id,
                action="mute",
            )

    summary = f"Пользователь размучен. Успешно: {len(success_ids)}"
    if failed_ids:
        summary += f", ошибки: {len(failed_ids)}."
    await message.answer(summary)
@router.message(F.text.startswith("/warn"))
async def cmd_warn(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Обрабатывает cmd warn.

    Аргументы:
        message: Значение message.
        sessionmaker: Значение sessionmaker.
        settings: Значение settings.
    """
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not message.from_user:
        return

    is_staff = await _is_staff(sessionmaker, message.from_user.id)
    if not is_staff:
        if not await _is_moderated_chat(sessionmaker, message.chat.id):
            await message.answer(
                "Чат не в списке модерации. Используйте /mod_chat_add."
            )
            return
        if not await _has_moderation_rights(
            message.bot, sessionmaker, message.chat.id, message.from_user.id
        ):
            await message.answer("Нет доступа.")
            return

    parts = (message.text or "").split()
    target_identifier, reason = _parse_target_and_reason(message, parts[1:])
    target_id = await _resolve_user_identifier(message.bot, target_identifier)
    if not target_id:
        await message.answer(
            "Формат: /warn user_id/@username [причина] или ответом на пересланное сообщение."
        )
        return

    chat_ids = await _get_moderated_chat_ids(sessionmaker)
    if not chat_ids:
        await message.answer("Нет активных чатов модерации.")
        return
    multi_chat = len(chat_ids) > 1

    warn_counts: dict[int, int] = {}
    async with sessionmaker() as session:
        for chat_id in chat_ids:
            result = await session.execute(
                select(ModerationWarn)
                .where(
                    ModerationWarn.chat_id == chat_id,
                    ModerationWarn.user_id == target_id,
                )
                .order_by(ModerationWarn.id.desc())
            )
            warn_record = result.scalars().first()
            if warn_record:
                warn_record.count = (warn_record.count or 0) + 1
            else:
                warn_record = ModerationWarn(
                    chat_id=chat_id,
                    user_id=target_id,
                    count=1,
                )
                session.add(warn_record)
            warn_counts[chat_id] = warn_record.count or 0
        await session.commit()

    reason_text = _normalize_reason(reason)
    max_warn = max(warn_counts.values()) if warn_counts else 0
    chat_label = (
        f"Все модерируемые чаты ({len(chat_ids)})"
        if multi_chat
        else f"{message.chat.title or '-'} ({message.chat.id})"
    )
    mod_label = _format_tg_user(message.from_user)
    target_label = (
        _format_tg_user(message.reply_to_message.from_user)
        if message.reply_to_message and message.reply_to_message.from_user
        else str(target_id)
    )
    warn_log_text = (
        "⚠️ Модерация\n"
        "Действие: предупреждение\n"
        f"Чаты: {chat_label}\n"
        f"Модератор: {mod_label}\n"
        f"Пользователь: {target_label}\n"
        f"Счетчик: {max_warn}/3\n"
        f"Причина: {reason_text}"
    )
    await _log_info(message.bot, settings, warn_log_text)

    mute_chat_ids = [chat_id for chat_id, count in warn_counts.items() if count >= 3]
    mute_success: list[int] = []
    mute_failed: list[int] = []
    
    if mute_chat_ids:
        until_date = datetime.now(timezone.utc) + timedelta(days=7)
        permissions = ChatPermissions(can_send_messages=False)
        for chat_id in mute_chat_ids:
            try:
                await message.bot.restrict_chat_member(
                    chat_id,
                    target_id,
                    permissions=permissions,
                    until_date=until_date,
                )
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                try:
                    await message.bot.restrict_chat_member(
                        chat_id,
                        target_id,
                        permissions=permissions,
                        until_date=until_date,
                    )
                except Exception:
                    mute_failed.append(chat_id)
                    continue
            except Exception:
                mute_failed.append(chat_id)
                continue
            mute_success.append(chat_id)

        auto_reason = (
            f"3 предупреждения: {reason_text}"
            if reason_text != "Причина не указана"
            else "3 предупреждения"
        )
        applied_trust = False
        async with sessionmaker() as session:
            for chat_id in mute_success:
                record, created = await _upsert_restriction(
                    session,
                    chat_id=chat_id,
                    user_id=target_id,
                    action="mute",
                    reason=auto_reason,
                    until_date=until_date,
                    created_by=message.from_user.id,
                )
                if created and not applied_trust:
                    await apply_trust_event(
                        session,
                        target_id,
                        "chat_mute",
                        -5,
                        record.reason or "Мут в чате",
                        ref_type="restriction",
                        ref_id=record.id,
                    )
                    applied_trust = True
                
                result = await session.execute(
                    select(ModerationWarn)
                    .where(
                        ModerationWarn.chat_id == chat_id,
                        ModerationWarn.user_id == target_id,
                    )
                    .order_by(ModerationWarn.id.desc())
                )
                warn_record = result.scalars().first()
                if warn_record:
                    warn_record.count = 0
            await session.commit()

        until_label = until_date.strftime("%Y-%m-%d %H:%M UTC")
        mute_multi_chat = len(mute_success) > 1
        mute_chat_label = (
            f"Все модерируемые чаты ({len(mute_success)})"
            if mute_multi_chat
            else f"{message.chat.title or '-'} ({message.chat.id})"
        )
        mute_log_text = (
            "⛔ Модерация\n"
            "Дейсвие: мут (3 предупреждения)\n"
            f"Чаты: {mute_chat_label}\n"
            f"Модератор: {mod_label}\n"
            f"Пользователь: {target_label}\n"
            f"Срок: 7d (до {until_label})\n"
            f"Причина: {auto_reason}"
        )
        if mute_failed:
            mute_log_text += f"\nОшибки: {len(mute_failed)}"
        await _log_info(message.bot, settings, mute_log_text)

    if mute_success:
        try:
            if len(mute_success) > 1:
                dm_text = (
                    "Вы получили 3 предупреждения в чатах модерации. "
                    "Вам выдан мут на 7 дней."
                )
            else:
                chat_title = message.chat.title or "-"
                dm_text = (
                    f"Вы получили 3 предупреждения в чате {chat_title}. "
                    "Вам выдан мут на 7 дней."
                )
            await message.bot.send_message(target_id, dm_text)
        except Exception:
            pass
        summary = (
            f"Пользователь получил 3 предупреждения и замучен на 7 дней. "
            f"Мутов: {len(mute_success)}"
        )
        if mute_failed:
            summary += f", ошибки: {len(mute_failed)}."
        await message.answer(summary)
        return

    try:
        if multi_chat:
            dm_text = (
                f"Вам вынесено предупреждение в чатах модерации. "
                f"Причина: {reason_text}. Текущий счетчик: {max_warn}/3."
            )
        else:
            chat_title = message.chat.title or "-"
            dm_text = (
                f"Вам вынесено предупреждение в чате {chat_title}. "
                f"Причина: {reason_text}. Текущий счетчик: {max_warn}/3."
            )
        await message.bot.send_message(target_id, dm_text)
    except Exception:
        pass

    summary = f"Пользователь получил предупреждение ({max_warn}/3)."
    await message.answer(summary)
@router.message(F.chat.type.in_({"group", "supergroup"}) & ~F.text.startswith("/"))
async def moderate_chat(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Обрабатывает moderate chat.

    Аргументы:
        message: Значение message.
        sessionmaker: Значение sessionmaker.
        settings: Значение settings.
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
                reason="Автобан: запрещенные слова",
            )
            if created:
                await apply_trust_event(
                    session,
                    message.from_user.id,
                    "chat_ban",
                    -20,
                    record.reason or "Бан в чате",
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
                    "Ваше сообщение нарушает правила. "
                    "Вы заблокированы в чате. "
                    f"Причина: {reason_text}"
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
                "Сообщение отправлено на рассмотрение.",
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
    """Обрабатывает mod case ok.

    Аргументы:
        callback: Значение callback.
        sessionmaker: Значение sessionmaker.
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
    """Обрабатывает mod case cancel.

    Аргументы:
        callback: Значение callback.
        sessionmaker: Значение sessionmaker.
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
