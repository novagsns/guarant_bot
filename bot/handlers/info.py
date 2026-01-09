"""Module for info functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import User
from bot.keyboards.info import (
    faq_back_kb,
    faq_list_kb,
    info_back_kb,
    info_menu_kb,
)
from bot.utils.roles import role_label

router = Router()


FAQ_ANSWERS = {
    "guarantee": (
        "🤝 <b>Гарант‑сделка</b> — безопасная сделка. "
        "Гарант GSNS контролирует передачу аккаунта и оплату."
    ),
    "create_ad": (
        "📦 Чтобы разместить объявление: откройте «Сделки и объявления» → "
        "«Продать аккаунт» и следуйте шагам."
    ),
    "topup": (
        "💳 Чтобы пополнить GSNS Coins: «Услуги сети» → «Пополнить GSNS Coins», "
        "введите сумму, подтвердите и загрузите чек."
    ),
    "fees": (
        "💰 Комиссия зависит от типа сделки и суммы. "
        "Актуальные условия указаны в /start и в разделе «Информация»."
    ),
}


@router.message(F.text == "ℹ️ Информация")
async def info_menu(message: Message) -> None:
    """Handle info menu.

    Args:
        message: Value for message.
    """
    text = "<b>ℹ️ Информация GSNS</b>\nВыберите раздел ниже."
    await message.answer(text, reply_markup=info_menu_kb())


@router.callback_query(F.data == "info:back")
async def info_back(callback: CallbackQuery) -> None:
    """Handle info back.

    Args:
        callback: Value for callback.
    """
    await callback.message.edit_text(
        "<b>ℹ️ Информация GSNS</b>\nВыберите раздел ниже.",
        reply_markup=info_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:faq")
async def info_faq(callback: CallbackQuery) -> None:
    """Handle info faq.

    Args:
        callback: Value for callback.
    """
    await callback.message.edit_text(
        "❓ <b>FAQ</b>\nВыберите вопрос:",
        reply_markup=faq_list_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("faq:"))
async def faq_answer(callback: CallbackQuery) -> None:
    """Handle faq answer.

    Args:
        callback: Value for callback.
    """
    key = callback.data.split(":")[1]
    answer = FAQ_ANSWERS.get(key, "Вопрос не найден.")
    await callback.message.edit_text(answer, reply_markup=faq_back_kb())
    await callback.answer()


@router.callback_query(F.data == "info:privacy")
async def info_privacy(callback: CallbackQuery) -> None:
    """Handle info privacy.

    Args:
        callback: Value for callback.
    """
    text = (
        "🔒 <b>Политика конфиденциальности GSNS</b>\n\n"
        "• Данные используются только для работы сервиса.\n"
        "• Переписки внутри бота защищены и не публикуются.\n"
        "• GSNS оставляет за собой право блокировать пользователей за нарушения.\n"
        "• Использование сервиса означает согласие с правилами."
    )
    await callback.message.edit_text(text, reply_markup=info_back_kb())
    await callback.answer()


@router.callback_query(F.data == "info:staff")
async def info_staff(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    """Handle info staff.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(
                User.role.in_({"owner", "admin", "moderator", "designer", "guarantor"})
            )
        )
        users = result.scalars().all()

    role_order = ["owner", "admin", "moderator", "guarantor", "designer"]
    grouped = {role: [] for role in role_order}
    for user in users:
        if user.role in grouped:
            grouped[user.role].append(user)

    lines = [
        "👥 <b>Команда GSNS</b>",
        "—",
        "👑 <b>Основатель</b>",
        "• @nsim_GSNS",
    ]
    for role in role_order:
        members = grouped.get(role) or []
        if not members:
            continue
        title = role_label(role)
        names = []
        for member in members:
            names.append(
                f"@{member.username}" if member.username else f"id:{member.id}"
            )
        lines.append(f"💼 <b>{title}</b>")
        lines.append(f"• {', '.join(names)}")

    await callback.message.edit_text("\n".join(lines), reply_markup=info_back_kb())
    await callback.answer()


@router.callback_query(F.data == "info:guards")
async def info_guards(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle info guards.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.role == "guarantor"))
        users = result.scalars().all()

    if not users:
        await callback.message.edit_text(
            "Гаранты пока не назначены.", reply_markup=info_back_kb()
        )
        await callback.answer()
        return

    on_shift = []
    off_shift = []
    for user in users:
        name = f"@{user.username}" if user.username else f"id:{user.id}"
        if user.on_shift:
            on_shift.append(name)
        else:
            off_shift.append(name)

    text = "🟢 <b>Гаранты на смене</b>:\n"
    text += "\n".join(f"• {name}" for name in on_shift) if on_shift else "• нет"
    text += "\n\n🔴 <b>Гаранты не на смене</b>:\n"
    text += "\n".join(f"• {name}" for name in off_shift) if off_shift else "• нет"

    await callback.message.edit_text(text, reply_markup=info_back_kb())
    await callback.answer()
