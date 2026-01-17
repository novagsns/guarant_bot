"""Module for info functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
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
        "<b>1. Какие данные мы получаем</b>\n"
        "• Telegram ID, username, имя профиля.\n"
        "• Данные объявлений, сделок, отзывов, жалоб и обращений в поддержку.\n"
        "• Платежные события: сумма, валюта, время, статус (без хранения реквизитов).\n"
        "• Технические логи для защиты сервиса и качества работы.\n\n"
        "<b>2. Зачем это нужно</b>\n"
        "• Для безопасности сделок, подтверждений и уведомлений.\n"
        "• Для обслуживания пользователей и связи по заявкам.\n"
        "• Для предотвращения мошенничества и споров.\n\n"
        "<b>3. Что мы не делаем</b>\n"
        "• Не продаем и не передаем данные третьим лицам.\n"
        "• Не публикуем личные переписки и материалы.\n\n"
        "<b>4. Защита и хранение</b>\n"
        "• Данные хранятся на защищенной инфраструктуре.\n"
        "• Доступ ограничен и используется только для поддержки сервиса.\n\n"
        "<b>5. Сроки хранения</b>\n"
        "• Данные хранятся столько, сколько нужно для работы сервиса и безопасности.\n"
        "• Вы можете запросить удаление профиля через поддержку.\n\n"
        "<b>6. Ваши права</b>\n"
        "• Запросить исправление или удаление данных.\n"
        "• Получить ответ по обращению в разумные сроки.\n\n"
        "<b>7. Контакт</b>\n"
        "• По любым вопросам пишите в поддержку GSNS.\n\n"
        "Использование сервиса означает согласие с этой политикой."
    )
    await callback.message.edit_text(text, reply_markup=info_back_kb())
    await callback.answer()


@router.callback_query(F.data == "info:order")
async def info_order(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle info order."""
    owner_ids = list(settings.owner_ids or [])
    owners_by_id: dict[int, User] = {}

    if owner_ids:
        async with sessionmaker() as session:
            result = await session.execute(
                select(User).where(User.id.in_(owner_ids))
            )
            owners = result.scalars().all()
            owners_by_id = {owner.id: owner for owner in owners}
    if not owner_ids:
        async with sessionmaker() as session:
            result = await session.execute(select(User).where(User.role == "owner"))
            owners = result.scalars().all()
            owners_by_id = {owner.id: owner for owner in owners}
        owner_ids = list(owners_by_id.keys())

    owner_labels: list[str] = []
    updated: dict[int, tuple[str, str | None]] = {}
    for owner_id in owner_ids:
        username = None
        full_name = None
        try:
            chat = await callback.bot.get_chat(owner_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            chat = None
        if chat:
            username = chat.username
            full_name = getattr(chat, "full_name", None)
        if not username:
            owner = owners_by_id.get(owner_id)
            if owner and owner.username:
                username = owner.username
                full_name = full_name or owner.full_name
        if username:
            owner_labels.append(f"@{username}")
            owner = owners_by_id.get(owner_id)
            if owner and owner.username != username:
                updated[owner_id] = (username, full_name)
        else:
            owner_labels.append(f"id:{owner_id}")

    if updated:
        async with sessionmaker() as session:
            for owner_id, (username, full_name) in updated.items():
                owner = await session.get(User, owner_id)
                if not owner:
                    continue
                owner.username = username
                if full_name:
                    owner.full_name = full_name
            await session.commit()

    owner_text = ", ".join(owner_labels) if owner_labels else "—"

    text = (
        "🛠 <b>Заказ и разработка ботов</b>\n\n"
        "Создаю ботов и автоматизации под ваши задачи: от идеи до запуска.\n"
        "Помогаю упаковать продукт, прописать сценарии, сделать удобный UX и\n"
        "встроить оплату, подписки, аналитику, CRM и поддержку.\n\n"
        "<b>Что вы получаете</b>\n"
        "• Проектирование логики и пользовательских сценариев.\n"
        "• Чистый интерфейс и быстрые ответы для пользователей.\n"
        "• Интеграции оплат, уведомлений, админ‑панели и статистики.\n"
        "• Сопровождение после запуска и развитие продукта.\n\n"
        f"<b>Владелец/контакт:</b> {owner_text}\n"
        "Пишите в поддержку или напрямую — обсудим задачу и сроки."
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

    lines = ["👥 <b>Команда GSNS</b>"]
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
