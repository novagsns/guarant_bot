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


def _looks_like_target_token(value: str) -> bool:
    if not value:
        return False
    stripped = value.lstrip("@")
    if not stripped:
        return False
    return stripped.isdigit() or value.startswith("@") or _looks_like_username(value)


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
            if _looks_like_target_token(raw_id):
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


async def _resolve_user_identifier(
    bot,
    identifier: int | str | None,
    *,
    sessionmaker: async_sessionmaker | None = None,
) -> int | None:
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
        user = None
    if user:
        return user.id
    if sessionmaker:
        async with sessionmaker() as session:
            result = await session.execute(
                select(User.id).where(User.username == username)
            )
            user_id = result.scalar_one_or_none()
            return user_id
    return None


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
