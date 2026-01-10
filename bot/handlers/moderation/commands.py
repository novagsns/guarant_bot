# -*- coding: utf-8 -*-
"""Moderation command handlers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import ChatPermissions, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import ModerationWarn
from bot.handlers.chat_moderation import (
    _deactivate_restriction,
    _format_tg_user,
    _get_moderated_chat_ids,
    _has_moderation_rights,
    _is_moderated_chat,
    _is_staff,
    _log_info,
    _looks_like_target_token,
    _normalize_reason,
    _parse_duration,
    _parse_target_and_reason,
    _resolve_user_identifier,
    _upsert_moderation_chat,
    _upsert_restriction,
)
from bot.services.trust import apply_trust_event

router = Router()


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
    """Обрабатывает cmd ban."""
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
    if target_identifier is None:
        await message.answer(
            "Формат: /ban user_id/@username [причина] или ответом на пересланное сообщение."
        )
        return
    target_id = await _resolve_user_identifier(
        message.bot,
        target_identifier,
        sessionmaker=sessionmaker,
    )
    if target_id is None:
        await message.answer(
            "Не удалось найти пользователя. Укажите user_id или перешлите сообщение."
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
    """Обрабатывает cmd unban."""
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
    if target_identifier is None:
        await message.answer(
            "Формат: /unban user_id/@username или ответом на пересланное сообщение."
        )
        return
    target_id = await _resolve_user_identifier(
        message.bot,
        target_identifier,
        sessionmaker=sessionmaker,
    )
    if target_id is None:
        await message.answer(
            "Не удалось найти пользователя. Укажите user_id или перешлите сообщение."
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
    """Обрабатывает cmd mute."""
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
    first_arg = parts[1] if len(parts) > 1 else ""
    if first_arg and _looks_like_target_token(first_arg):
        if len(parts) < 3:
            await message.answer(
                "Формат: /mute user_id/@username 1h/2d [причина] или ответом на пересланное сообщение."
            )
            return
        target_identifier = first_arg
        duration_token = parts[2]
        reason = " ".join(parts[3:]).strip() if len(parts) > 3 else "-"
    elif message.reply_to_message and message.reply_to_message.forward_from:
        duration_token = first_arg
        reason = " ".join(parts[2:]).strip() if len(parts) > 2 else "-"
        target_identifier = message.reply_to_message.forward_from.id
    else:
        await message.answer(
            "Формат: /mute user_id/@username 1h/2d [причина] или ответом на пересланное сообщение."
        )
        return

    target_id = await _resolve_user_identifier(
        message.bot,
        target_identifier,
        sessionmaker=sessionmaker,
    )
    if target_id is None:
        await message.answer(
            "Не удалось найти пользователя. Укажите user_id или перешлите сообщение."
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
    """Обрабатывает cmd unmute."""
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
    if target_identifier is None:
        await message.answer(
            "Формат: /unmute user_id/@username или ответом на пересланное сообщение."
        )
        return
    target_id = await _resolve_user_identifier(
        message.bot,
        target_identifier,
        sessionmaker=sessionmaker,
    )
    if target_id is None:
        await message.answer(
            "Не удалось найти пользователя. Укажите user_id или перешлите сообщение."
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
    """Обрабатывает cmd warn."""
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
    if target_identifier is None:
        await message.answer(
            "Формат: /warn user_id/@username [причина] или ответом на пересланное сообщение."
        )
        return
    target_id = await _resolve_user_identifier(
        message.bot,
        target_identifier,
        sessionmaker=sessionmaker,
    )
    if target_id is None:
        await message.answer(
            "Не удалось найти пользователя. Укажите user_id или перешлите сообщение."
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
