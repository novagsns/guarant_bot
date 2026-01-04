"""Module for services functionality."""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import random

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    RouletteSpin,
    Service,
    ServicePurchase,
    TopUp,
    User,
    WalletTransaction,
)
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.common import referral_kb
from bot.keyboards.services import (
    my_service_kb,
    roulette_result_kb,
    service_buy_kb,
    service_chat_kb,
    service_list_kb,
    services_menu_kb,
    topup_confirm_kb,
    topup_reject_reason_kb,
    topup_review_kb,
)
from bot.utils.admin_target import get_admin_target
from bot.utils.moderation import contains_prohibited
from bot.utils.roles import is_owner

router = Router()

ROULETTE_SPIN_COST = Decimal("500")
ROULETTE_BIG_WIN_AMOUNT = Decimal("5000")


class ServiceCreateStates(StatesGroup):
    """Represent ServiceCreateStates.

    Attributes:
        category: Attribute value.
        title: Attribute value.
        description: Attribute value.
        media_type: Attribute value.
        media: Attribute value.
        price: Attribute value.
    """

    category = State()
    title = State()
    description = State()
    media_type = State()
    media = State()
    price = State()


class ServiceEditStates(StatesGroup):
    """Represent ServiceEditStates.

    Attributes:
        value: Attribute value.
        service_id: Attribute value.
        media_type: Attribute value.
        media: Attribute value.
    """

    value = State()
    service_id = State()
    media_type = State()
    media = State()


class TopUpStates(StatesGroup):
    """Represent TopUpStates.

    Attributes:
        amount: Attribute value.
        confirm: Attribute value.
        receipt: Attribute value.
    """

    amount = State()
    confirm = State()
    receipt = State()


class TopUpRejectStates(StatesGroup):
    """Represent TopUpRejectStates.

    Attributes:
        reason: Attribute value.
        topup_id: Attribute value.
    """

    reason = State()
    topup_id = State()


class ServiceChatStates(StatesGroup):
    """Represent ServiceChatStates.

    Attributes:
        in_chat: Attribute value.
    """

    in_chat = State()


def _roll_roulette(
    *,
    skin_prob: Decimal,
    big_win_prob: Decimal,
) -> tuple[str, Decimal]:
    """Handle roll roulette.

    Args:
        skin_prob: Probability of the skin prize.
        big_win_prob: Probability of the big win.

    Returns:
        Return value.
    """
    roll = random.random()
    big_prob = big_win_prob
    roll_dec = Decimal(str(roll))
    if roll_dec < skin_prob:
        return "skin", Decimal("0")
    if roll_dec < skin_prob + big_prob:
        return "coins", ROULETTE_BIG_WIN_AMOUNT
    return "coins", Decimal(str(random.randint(0, 500)))


async def _animate_roulette(message: Message) -> None:
    """Render a roulette animation without spamming the chat."""
    variants = [
        ["🎰 Крутим", "🎰 Крутим.", "🎰 Крутим..", "🎰 Крутим..."],
        ["🎰 Запуск", "🎰 Вращение", "🎰 Почти...", "🎰 Стоп!"],
        [
            "🎰 Крутим",
            "🎰 Крутим.",
            "🎰 Крутим..",
            "🎰 Крутим...",
            "🎰 Крутим....",
            "🎰 Стоп!",
        ],
    ]
    frames = random.choice(variants)
    for frame in frames:
        await asyncio.sleep(0.5)
        try:
            await message.edit_text(frame)
        except Exception:
            return


@router.callback_query(F.data == "roulette:start")
async def roulette_start(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle roulette start.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    cost = ROULETTE_SPIN_COST
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if (user.balance or 0) < cost:
            await callback.message.answer("Недостаточно GSNS Coins.")
            await callback.answer()
            return
        user.balance = (user.balance or 0) - cost
        session.add(
            WalletTransaction(
                user_id=user.id,
                amount=-cost,
                type="roulette_spin",
                description="Рулетка: крутка",
            )
        )

        prize_type, prize_amount = _roll_roulette(
            skin_prob=settings.roulette_skin_prob,
            big_win_prob=settings.roulette_big_win_prob,
        )
        if prize_type == "coins" and prize_amount > 0:
            user.balance = (user.balance or 0) + prize_amount
            session.add(
                WalletTransaction(
                    user_id=user.id,
                    amount=prize_amount,
                    type="roulette_win",
                    description="Рулетка: выигрыш",
                )
            )

        spin = RouletteSpin(
            user_id=user.id,
            cost=cost,
            prize_type=prize_type,
            prize_amount=prize_amount,
        )
        session.add(spin)
        await session.commit()

        new_balance = Decimal(str(user.balance or 0))

    spin_message = callback.message
    if spin_message:
        try:
            await spin_message.edit_text("🎰 Крутим...")
        except Exception:
            spin_message = await callback.message.answer("🎰 Крутим...")
    else:
        spin_message = await callback.message.answer("🎰 Крутим...")
    await _animate_roulette(spin_message)
    prize_fund_text = (
        "Призы рулетки:\n"
        "🎯 0–500 GSNS Coins\n"
        "💥 5000 GSNS Coins — джекпот\n"
        "🎁 Скин события"
    )

    if prize_type == "skin":
        await spin_message.edit_text(
            "🎁 Приз: Скин события\n"
            f"Баланс: {new_balance} GSNS Coins\n"
            f"{prize_fund_text}\n"
            "Мы свяжемся с вами.",
            reply_markup=roulette_result_kb(),
        )
        chat_id, topic_id = get_admin_target(settings)
        if chat_id != 0:
            await callback.bot.send_message(
                chat_id,
                (
                    "Рулетка: выигран скин события\n"
                    f"Пользователь: {callback.from_user.id}\n"
                    f"Spin ID: {spin.id}"
                ),
                message_thread_id=topic_id,
            )
    elif prize_amount > 0:
        title = "🎉 Выигрыш!"
        if prize_amount >= ROULETTE_BIG_WIN_AMOUNT:
            title = "💥 Джекпот!"
        await spin_message.edit_text(
            f"{title}\n"
            f"+{prize_amount} GSNS Coins\n"
            f"Баланс: {new_balance} GSNS Coins\n"
            f"{prize_fund_text}",
            reply_markup=roulette_result_kb(),
        )
    else:
        await spin_message.edit_text(
            "Увы, не повезло. Попробуйте снова!\n"
            f"Баланс: {new_balance} GSNS Coins\n"
            f"{prize_fund_text}",
            reply_markup=roulette_result_kb(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("service_chat:"))
async def service_chat_open(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle service chat open.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    purchase_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(ServicePurchase).where(ServicePurchase.id == purchase_id)
        )
        purchase = result.scalar_one_or_none()
        if not purchase:
            await callback.answer("Покупка не найдена.")
            return
        result = await session.execute(
            select(Service).where(Service.id == purchase.service_id)
        )
        service = result.scalar_one_or_none()
        if not service:
            await callback.answer("Услуга не найдена.")
            return
        if callback.from_user.id not in {purchase.buyer_id, service.creator_id}:
            await callback.answer("Нет доступа.")
            return

    role = "buyer" if callback.from_user.id == purchase.buyer_id else "seller"
    await state.set_state(ServiceChatStates.in_chat)
    await state.update_data(purchase_id=purchase_id, role=role)
    await callback.message.answer(
        f"Чат по покупке #{purchase_id} открыт. /exit для выхода."
    )
    await callback.answer()


@router.message(ServiceChatStates.in_chat)
async def service_chat_relay(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle service chat relay.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    purchase_id = data.get("purchase_id")
    role = data.get("role")
    text = message.text or message.caption
    if text and contains_prohibited(text):
        await message.answer(
            "Ссылки и контакты в чате GSNS запрещены. Удалите @/ссылки и попробуйте снова."
        )
        return

    if message.text and message.text.strip() == "/exit":
        await state.clear()
        await message.answer("Вы вышли из чата.")
        return

    async with sessionmaker() as session:
        result = await session.execute(
            select(ServicePurchase).where(ServicePurchase.id == purchase_id)
        )
        purchase = result.scalar_one_or_none()
        if not purchase:
            await message.answer("Покупка не найдена.")
            await state.clear()
            return
        result = await session.execute(
            select(Service).where(Service.id == purchase.service_id)
        )
        service = result.scalar_one_or_none()
        if not service:
            await message.answer("Услуга не найдена.")
            await state.clear()
            return

    if role == "buyer":
        target_id = service.creator_id
        prefix = "Покупатель:"
    else:
        target_id = purchase.buyer_id
        prefix = "Админ:"

    if message.photo:
        await message.bot.send_photo(
            target_id, message.photo[-1].file_id, caption=prefix
        )
        return
    if message.video:
        await message.bot.send_video(target_id, message.video.file_id, caption=prefix)
        return
    if message.document:
        await message.bot.send_document(
            target_id, message.document.file_id, caption=prefix
        )
        return
    await message.bot.send_message(target_id, f"{prefix} {message.text}")


def _is_admin(role: str) -> bool:
    """Handle is admin.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    return role in {"owner", "admin"}


@router.message(F.text == "🛒 Услуги сети")
async def services_menu(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle services menu.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        is_admin = _is_admin(user.role) or is_owner(
            user.role, settings.owner_ids, user.id
        )
    await message.answer(
        "Раздел услуг GSNS:",
        reply_markup=services_menu_kb(is_admin, str(ROULETTE_SPIN_COST)),
    )
    await message.answer(f"Стоимость крутки: {ROULETTE_SPIN_COST} GSNS Coins.")
    await message.answer(
        "Выгодный донат для вашей игры:",
        reply_markup=referral_kb(),
    )


@router.callback_query(F.data.startswith("services:"))
async def services_category(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    state: FSMContext,
) -> None:
    """Handle services category.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
        state: Value for state.
    """
    action = callback.data.split(":")[1]
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        is_admin_user = _is_admin(user.role) or is_owner(
            user.role, settings.owner_ids, user.id
        )

    if action == "add":
        if not is_admin_user:
            await callback.answer("Нет доступа.")
            return
        await _start_service_create(callback, state)
        return

    if action == "menu":
        await callback.message.edit_text(
            "Раздел услуг GSNS:",
            reply_markup=services_menu_kb(is_admin_user, str(ROULETTE_SPIN_COST)),
        )
        await callback.answer()
        return

    if action == "mine":
        await _show_my_services(callback, sessionmaker)
        return

    await _show_services_by_category(callback, sessionmaker, action)


async def _start_service_create(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle start service create.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await state.set_state(ServiceCreateStates.category)
    await callback.message.answer("Категория (exclusive/accounts/services):")
    await callback.answer()


@router.message(ServiceCreateStates.category)
async def service_category(message: Message, state: FSMContext) -> None:
    """Handle service category.

    Args:
        message: Value for message.
        state: Value for state.
    """
    category = message.text.strip().lower()
    if category not in {"exclusive", "accounts", "services"}:
        await message.answer("Категория: exclusive, accounts, services.")
        return
    await state.update_data(category=category)
    await state.set_state(ServiceCreateStates.title)
    await message.answer("Название услуги:")


@router.message(ServiceCreateStates.title)
async def service_title(message: Message, state: FSMContext) -> None:
    """Handle service title.

    Args:
        message: Value for message.
        state: Value for state.
    """
    await state.update_data(title=message.text.strip())
    await state.set_state(ServiceCreateStates.description)
    await message.answer("Описание услуги:")


@router.message(ServiceCreateStates.description)
async def service_description(message: Message, state: FSMContext) -> None:
    """Handle service description.

    Args:
        message: Value for message.
        state: Value for state.
    """
    await state.update_data(description=message.text.strip())
    await state.set_state(ServiceCreateStates.media_type)
    await message.answer("Добавить медиа? Напишите: Фото, Видео или Пропустить.")


@router.message(ServiceCreateStates.media_type)
async def service_media_type(message: Message, state: FSMContext) -> None:
    """Handle service media type.

    Args:
        message: Value for message.
        state: Value for state.
    """
    choice = message.text.strip().lower()
    if choice == "пропустить":
        await state.update_data(media_type=None, media_file_id=None)
        await state.set_state(ServiceCreateStates.price)
        await message.answer("Цена в GSNS Coins:")
        return
    if choice not in {"фото", "видео"}:
        await message.answer("Нужно выбрать: Фото, Видео или Пропустить.")
        return
    await state.update_data(media_type=choice)
    await state.set_state(ServiceCreateStates.media)
    await message.answer(f"Отправьте {choice}.")


@router.message(ServiceCreateStates.media)
async def service_media(message: Message, state: FSMContext) -> None:
    """Handle service media.

    Args:
        message: Value for message.
        state: Value for state.
    """
    data = await state.get_data()
    media_type = data.get("media_type")
    file_id = None
    if media_type == "фото":
        if not message.photo:
            await message.answer("Нужно отправить фото.")
            return
        file_id = message.photo[-1].file_id
    elif media_type == "видео":
        if not message.video:
            await message.answer("Нужно отправить видео.")
            return
        file_id = message.video.file_id
    else:
        await message.answer("Неверный тип медиа.")
        return
    await state.update_data(media_file_id=file_id)
    await state.set_state(ServiceCreateStates.price)
    await message.answer("Цена в GSNS Coins:")


@router.message(ServiceCreateStates.price)
async def service_price(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle service price.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    try:
        price = Decimal(message.text.replace(",", "."))
        if price <= 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        await message.answer("Неверная цена.")
        return

    data = await state.get_data()
    async with sessionmaker() as session:
        creator = await get_or_create_user(session, message.from_user)
        service = Service(
            creator_id=creator.id,
            category=data["category"],
            title=data["title"],
            description=data["description"],
            price=price,
            media_type=data.get("media_type"),
            media_file_id=data.get("media_file_id"),
        )
        session.add(service)
        await session.commit()

    await state.clear()
    await message.answer("Услуга добавлена.")


async def _show_services_by_category(
    callback: CallbackQuery, sessionmaker: async_sessionmaker, category: str
) -> None:
    """Handle show services by category.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        category: Value for category.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Service)
            .where(Service.category == category, Service.active.is_(True))
            .order_by(Service.id.desc())
        )
        services = result.scalars().all()

    if not services:
        await callback.message.answer("В этом разделе пока нет услуг.")
        await callback.answer()
        return

    buttons = [(service.id, service.title) for service in services]
    await callback.message.answer(
        "Выберите услугу:", reply_markup=service_list_kb(buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("service:"))
async def service_view(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle service view.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    service_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Service).where(Service.id == service_id))
        service = result.scalar_one_or_none()

    if not service or not service.active:
        await callback.answer("Услуга недоступна.")
        return

    caption = (
        f"{service.title}\n"
        f"Цена: {service.price} GSNS Coins\n\n"
        f"{service.description or ''}"
    )
    if service.media_type == "фото" and service.media_file_id:
        await callback.message.answer_photo(
            service.media_file_id,
            caption=caption,
            reply_markup=service_buy_kb(service.id),
        )
    elif service.media_type == "видео" and service.media_file_id:
        await callback.message.answer_video(
            service.media_file_id,
            caption=caption,
            reply_markup=service_buy_kb(service.id),
        )
    else:
        await callback.message.answer(caption, reply_markup=service_buy_kb(service.id))
    await callback.answer()


@router.callback_query(F.data.startswith("service_buy:"))
async def service_buy(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle service buy.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    service_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        buyer = await get_or_create_user(session, callback.from_user)
        result = await session.execute(select(Service).where(Service.id == service_id))
        service = result.scalar_one_or_none()
        if not service or not service.active:
            await callback.answer("Услуга недоступна.")
            return
        if (buyer.balance or 0) < service.price:
            await callback.answer("Недостаточно GSNS Coins.")
            return

        buyer.balance = (buyer.balance or 0) - service.price
        tx = WalletTransaction(
            user_id=buyer.id,
            amount=-service.price,
            type="purchase",
            description=f"Покупка услуги #{service.id}",
            ref_type="service_purchase",
        )
        session.add(tx)
        purchase = ServicePurchase(
            service_id=service.id, buyer_id=buyer.id, status="pending"
        )
        session.add(purchase)
        await session.flush()
        tx.ref_id = purchase.id
        if service.category == "accounts":
            service.active = False
        await session.commit()

        result = await session.execute(
            select(User).where(User.id == service.creator_id)
        )
        creator = result.scalar_one_or_none()

    await callback.message.answer("Покупка оформлена. Ожидайте связь.")
    await callback.message.answer(
        "Открыть чат с админом:",
        reply_markup=service_chat_kb(purchase.id),
    )
    if creator:
        buyer_label = (
            f"{buyer.id} (@{buyer.username})" if buyer.username else str(buyer.id)
        )
        await callback.bot.send_message(
            creator.id,
            (
                f"Покупка услуги #{service.id}\n"
                f"{service.title}\n"
                f"Покупатель: {buyer_label}"
            ),
            reply_markup=service_chat_kb(purchase.id),
        )
    await callback.answer()


async def _show_my_services(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle show my services.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Service)
            .where(Service.creator_id == callback.from_user.id)
            .order_by(Service.id.desc())
        )
        services = result.scalars().all()

    if not services:
        await callback.message.answer("У вас нет услуг.")
        await callback.answer()
        return

    for service in services:
        text = (
            f"{service.title}\n"
            f"Цена: {service.price} GSNS Coins\n"
            f"Статус: {'активна' if service.active else 'скрыта'}"
        )
        await callback.message.answer(text, reply_markup=my_service_kb(service.id))
    await callback.answer()


@router.callback_query(F.data.startswith("service_delete:"))
async def service_delete(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle service delete.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    service_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Service).where(Service.id == service_id))
        service = result.scalar_one_or_none()
        if not service or service.creator_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        await session.delete(service)
        await session.commit()
    await callback.message.answer("Услуга удалена.")
    await callback.answer()


@router.callback_query(F.data.startswith("service_edit:"))
async def service_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle service edit.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)
    await state.set_state(ServiceEditStates.value)
    await callback.message.answer("Введите новый формат: Название | Цена | Описание")
    await callback.answer()


@router.callback_query(F.data.startswith("service_media:"))
async def service_media_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle service media edit.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)
    await state.set_state(ServiceEditStates.media_type)
    await callback.message.answer("Выберите: Фото, Видео или Очистить.")
    await callback.answer()


@router.message(ServiceEditStates.media_type)
async def service_media_edit_type(message: Message, state: FSMContext) -> None:
    """Handle service media edit type.

    Args:
        message: Value for message.
        state: Value for state.
    """
    choice = message.text.strip().lower()
    if choice == "очистить":
        await state.update_data(media_type=None, media_file_id=None)
        await state.set_state(ServiceEditStates.media)
        await message.answer("Медиа будет удалено. Подтвердите любым сообщением.")
        return
    if choice not in {"фото", "видео"}:
        await message.answer("Нужно выбрать: Фото, Видео или Очистить.")
        return
    await state.update_data(media_type=choice)
    await state.set_state(ServiceEditStates.media)
    await message.answer(f"Отправьте {choice}.")


@router.message(ServiceEditStates.media)
async def service_media_edit_file(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle service media edit file.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    service_id = data.get("service_id")
    media_type = data.get("media_type")
    if not service_id:
        await state.clear()
        await message.answer("Сеанс редактирования истек.")
        return

    file_id = None
    if media_type == "фото":
        if not message.photo:
            await message.answer("Нужно отправить фото.")
            return
        file_id = message.photo[-1].file_id
    elif media_type == "видео":
        if not message.video:
            await message.answer("Нужно отправить видео.")
            return
        file_id = message.video.file_id

    async with sessionmaker() as session:
        result = await session.execute(select(Service).where(Service.id == service_id))
        service = result.scalar_one_or_none()
        if not service or service.creator_id != message.from_user.id:
            await message.answer("Нет доступа.")
            await state.clear()
            return

        if media_type is None:
            service.media_type = None
            service.media_file_id = None
        else:
            service.media_type = media_type
            service.media_file_id = file_id
        await session.commit()

    await state.clear()
    await message.answer("Медиа обновлено.")


@router.message(ServiceEditStates.value)
async def service_edit_value(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle service edit value.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    service_id = data.get("service_id")
    if not service_id:
        await state.clear()
        await message.answer("Сеанс редактирования истек.")
        return

    parts = [p.strip() for p in message.text.split("|")]
    if len(parts) < 3:
        await message.answer("Формат: Название | Цена | Описание")
        return
    title, price_raw, description = parts[0], parts[1], parts[2]
    try:
        price = Decimal(price_raw.replace(",", "."))
    except (InvalidOperation, AttributeError):
        await message.answer("Неверная цена.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Service).where(Service.id == service_id))
        service = result.scalar_one_or_none()
        if not service or service.creator_id != message.from_user.id:
            await message.answer("Нет доступа.")
            await state.clear()
            return
        service.title = title
        service.price = price
        service.description = description
        await session.commit()

    await state.clear()
    await message.answer("Услуга обновлена.")


@router.callback_query(F.data == "topup:start")
async def topup_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings
) -> None:
    """Handle topup start.

    Args:
        callback: Value for callback.
        state: Value for state.
        settings: Value for settings.
    """
    await state.clear()
    await state.set_state(TopUpStates.amount)
    wallet = settings.wallet_trc20 or "не настроено"
    coins_per_rub = settings.coins_per_rub
    usdt_rate = settings.usdt_rate_rub
    min_rub = settings.min_topup_rub
    usdt_per_rub = (Decimal("1") / usdt_rate).quantize(Decimal("0.0001"))
    min_usdt = (min_rub / usdt_rate).quantize(Decimal("0.0001"))
    min_coins = (min_rub * coins_per_rub).quantize(Decimal("0.01"))
    await callback.message.answer(
        "Пополнение GSNS Coins.\n"
        f"Кошелек TRC20: <code>{wallet}</code>\n"
        f"Курс: 1 ₽ = {usdt_per_rub} USDT = {coins_per_rub} Coins\n"
        f"Минимум: {min_rub} ₽ = {min_usdt} USDT = {min_coins} Coins\n"
        "Введите сумму в рублях эквивалентом:"
    )
    await callback.answer()


@router.message(TopUpStates.amount)
async def topup_amount(message: Message, state: FSMContext, settings: Settings) -> None:
    """Handle topup amount.

    Args:
        message: Value for message.
        state: Value for state.
        settings: Value for settings.
    """
    try:
        amount = Decimal(message.text.replace(",", "."))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        await message.answer("Неверная сумма.")
        return
    if amount < settings.min_topup_rub:
        await message.answer(f"Минимальная сумма {settings.min_topup_rub} ₽.")
        return
    usdt = (amount / settings.usdt_rate_rub).quantize(Decimal("0.0001"))
    coins = (amount * settings.coins_per_rub).quantize(Decimal("0.01"))
    await state.update_data(amount=amount)
    await state.update_data(usdt=usdt, coins=coins)
    await state.set_state(TopUpStates.confirm)
    await message.answer(
        f"Вы получите {coins} GSNS Coins\n"
        f"Эквивалент: {amount} ₽ ≈ {usdt} USDT\n"
        "Подтвердить?",
        reply_markup=topup_confirm_kb(),
    )


@router.callback_query(F.data == "topup_confirm:yes")
async def topup_confirm_yes(
    callback: CallbackQuery, state: FSMContext, settings: Settings
) -> None:
    """Handle topup confirm yes.

    Args:
        callback: Value for callback.
        state: Value for state.
        settings: Value for settings.
    """
    data = await state.get_data()
    amount = data.get("amount")
    usdt = data.get("usdt")
    coins = data.get("coins")
    if amount is None or usdt is None or coins is None:
        await state.clear()
        await callback.message.answer("Сеанс истек.")
        await callback.answer()
        return
    wallet = settings.wallet_trc20 or "не настроено"
    await state.set_state(TopUpStates.receipt)
    await callback.message.answer(
        "Реквизиты для оплаты:\n"
        f"<code>{wallet}</code>\n"
        f"Сумма: {amount} ₽ ≈ {usdt} USDT = {coins} Coins\n"
        "Отправьте чек (фото или документ)."
    )
    await callback.answer()


@router.callback_query(F.data == "topup_confirm:no")
async def topup_confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle topup confirm no.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await callback.message.answer("Пополнение отменено.")
    await callback.answer()


@router.message(TopUpStates.receipt)
async def topup_receipt(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle topup receipt.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    data = await state.get_data()
    amount = data.get("amount")
    usdt = data.get("usdt")
    coins = data.get("coins")
    if amount is None or usdt is None or coins is None:
        await state.clear()
        await message.answer("Сеанс истек.")
        return

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id

    if not file_id:
        await message.answer("Нужен чек (фото или документ).")
        return

    async with sessionmaker() as session:
        topup = TopUp(
            user_id=message.from_user.id,
            amount=coins,
            amount_rub=amount,
            amount_usdt=usdt,
            receipt_file_id=file_id,
        )
        session.add(topup)
        await session.commit()

    chat_id, topic_id = get_admin_target(settings)
    if chat_id != 0:
        caption = (
            f"Пополнение #{topup.id}\n"
            f"Пользователь: {message.from_user.id}\n"
            f"Сумма: {amount} ₽ ≈ {usdt} USDT = {coins} GSNS Coins"
        )
        if message.photo:
            await message.bot.send_photo(
                chat_id,
                topup.receipt_file_id,
                caption=caption,
                message_thread_id=topic_id,
                reply_markup=topup_review_kb(topup.id),
            )
        else:
            await message.bot.send_document(
                chat_id,
                topup.receipt_file_id,
                caption=caption,
                message_thread_id=topic_id,
                reply_markup=topup_review_kb(topup.id),
            )

    await state.clear()
    await message.answer("Заявка на пополнение отправлена.")


@router.callback_query(F.data.startswith("topup_ok:"))
async def topup_ok(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle topup ok.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    topup_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        reviewer = await get_or_create_user(session, callback.from_user)
        if not is_owner(reviewer.role, settings.owner_ids, reviewer.id):
            await callback.answer("Нет доступа.")
            return
        result = await session.execute(select(TopUp).where(TopUp.id == topup_id))
        topup = result.scalar_one_or_none()
        if not topup or topup.status != "pending":
            await callback.answer("Заявка не найдена.")
            return
        if topup.amount_rub is not None:
            expected = (topup.amount_rub * settings.coins_per_rub).quantize(
                Decimal("0.01")
            )
            actual = Decimal(str(topup.amount or 0))
            diff = (expected - actual).copy_abs()
            if diff > Decimal("0.01"):
                topup.status = "rejected"
                topup.reason = "Несоответствие суммы пополнения"
                topup.reviewer_id = reviewer.id
                await session.commit()
                await callback.answer("Сумма не совпадает. Требуется проверка.")
                chat_id, topic_id = get_admin_target(settings)
                if chat_id != 0:
                    await callback.bot.send_message(
                        chat_id,
                        (
                            f"Подозрительное пополнение #{topup.id}\n"
                            f"Пользователь: {topup.user_id}\n"
                            f"Ожидалось: {expected} Coins\n"
                            f"Фактически: {actual} Coins"
                        ),
                        message_thread_id=topic_id,
                    )
                return

        result = await session.execute(select(User).where(User.id == topup.user_id))
        user = result.scalar_one_or_none()
        if not user:
            await callback.answer("Пользователь не найден.")
            return

        user.balance = (user.balance or 0) + topup.amount
        topup.status = "approved"
        topup.reviewer_id = reviewer.id
        session.add(
            WalletTransaction(
                user_id=user.id,
                amount=topup.amount,
                type="topup",
                description=f"Пополнение #{topup.id}",
            )
        )
        await session.commit()

    await callback.message.answer("Пополнение одобрено.")
    await callback.bot.send_message(topup.user_id, f"Пополнение #{topup.id} одобрено.")
    await callback.answer()


@router.callback_query(F.data.startswith("topup_reject:"))
async def topup_reject(
    callback: CallbackQuery, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle topup reject.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    topup_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        reviewer = await get_or_create_user(session, callback.from_user)
        if not is_owner(reviewer.role, settings.owner_ids, reviewer.id):
            await callback.answer("Нет доступа.")
            return
    await callback.message.answer(
        "Выберите причину отказа:",
        reply_markup=topup_reject_reason_kb(topup_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("topup_reason:"))
async def topup_reject_reason(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle topup reject reason.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    _, reason_key, topup_id_raw = callback.data.split(":")
    topup_id = int(topup_id_raw)

    if reason_key == "other":
        await state.update_data(topup_id=topup_id)
        await state.set_state(TopUpRejectStates.reason)
        await callback.message.answer("Введите причину отказа:")
        await callback.answer()
        return

    reasons = {
        "amount": "Неверная сумма",
        "receipt": "Чек не читается",
        "data": "Недостаточно данных",
    }
    reason = reasons.get(reason_key, "Отказано")
    await _reject_topup(callback, sessionmaker, settings, topup_id, reason)


@router.message(TopUpRejectStates.reason)
async def topup_reject_custom(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle topup reject custom.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    data = await state.get_data()
    topup_id = data.get("topup_id")
    if not topup_id:
        await state.clear()
        await message.answer("Сеанс истек.")
        return
    reason = message.text.strip()
    await _reject_topup(message, sessionmaker, settings, topup_id, reason)
    await state.clear()


async def _reject_topup(
    event,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    topup_id: int,
    reason: str,
) -> None:
    """Handle reject topup.

    Args:
        event: Value for event.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
        topup_id: Value for topup_id.
        reason: Value for reason.
    """
    async with sessionmaker() as session:
        reviewer = await get_or_create_user(session, event.from_user)
        if not is_owner(reviewer.role, settings.owner_ids, reviewer.id):
            if isinstance(event, CallbackQuery):
                await event.answer("Нет доступа.")
            return
        result = await session.execute(select(TopUp).where(TopUp.id == topup_id))
        topup = result.scalar_one_or_none()
        if not topup or topup.status != "pending":
            if isinstance(event, CallbackQuery):
                await event.answer("Заявка не найдена.")
            return
        topup.status = "rejected"
        topup.reason = reason
        topup.reviewer_id = reviewer.id
        await session.commit()

    await event.bot.send_message(
        topup.user_id,
        f"Пополнение #{topup.id} отклонено. Причина: {reason}",
    )
    if isinstance(event, CallbackQuery):
        await event.message.answer("Пополнение отклонено.")
        await event.answer()
    else:
        await event.answer("Пополнение отклонено.")
