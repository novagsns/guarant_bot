"""Module for deals functionality."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import Ad, Deal, Dispute, Game, User
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.ads import (
    admin_take_deal_kb,
    contact_open_kb,
    deal_after_take_kb,
    prechat_action_kb,
    prechat_finish_kb,
    seller_price_kb,
)
from bot.keyboards.staff import confirm_action_kb
from bot.services.anon_chat import role_label
from bot.services.fees import calculate_fee
from bot.services.trust import get_trust_score, apply_trust_event
from bot.utils.admin_target import get_admin_target
from bot.utils.moderation import contains_prohibited
from bot.utils.vip import free_fee_active

router = Router()


class ChatStates(StatesGroup):
    """Represent ChatStates.

    Attributes:
        in_chat: Attribute value.
    """

    in_chat = State()


class DisputeStates(StatesGroup):
    """Represent DisputeStates.

    Attributes:
        reason: Attribute value.
    """

    reason = State()


class ExchangeStates(StatesGroup):
    """Represent ExchangeStates.

    Attributes:
        addon: Attribute value.
        description: Attribute value.
    """

    addon = State()
    description = State()


class PreChatStates(StatesGroup):
    """Represent PreChatStates.

    Attributes:
        in_chat: Attribute value.
        buy_price: Attribute value.
    """

    in_chat = State()
    buy_price = State()


class SellerPriceStates(StatesGroup):
    """Represent SellerPriceStates.

    Attributes:
        change_price: Attribute value.
    """

    change_price = State()


class DealSendStates(StatesGroup):
    """Represent DealSendStates.

    Attributes:
        data: Attribute value.
        payment: Attribute value.
    """

    data = State()
    payment = State()


async def _send_admin_deal(
    bot,
    settings: Settings,
    text: str,
    deal_id: int,
) -> None:
    """Handle send admin deal.

    Args:
        bot: Value for bot.
        settings: Value for settings.
        text: Value for text.
        deal_id: Value for deal_id.
    """
    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        return
    await bot.send_message(
        chat_id,
        text,
        message_thread_id=topic_id,
        reply_markup=admin_take_deal_kb(deal_id),
    )


async def _format_user(user: User) -> str:
    """Handle format user.

    Args:
        user: Value for user.

    Returns:
        Return value.
    """
    if user.username:
        return f"@{user.username}"
    return f"id:{user.id}"


def _price_to_cents(value: Decimal) -> int:
    """Handle price to cents.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    return int((value * Decimal("100")).to_integral_value())


def _cents_to_price(value: int) -> Decimal:
    """Handle cents to price.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    return (Decimal(value) / Decimal("100")).quantize(Decimal("0.01"))


def _exchange_checklists() -> tuple[str, str, str]:
    """Handle exchange checklists.

    Returns:
        Return value.
    """
    buyer_text = (
        "🧾 <b>Чек‑лист обмена (покупатель)</b>\n"
        "☐ Отправить гаранту ID аккаунта\n"
        "☐ Отправить скриншоты и данные для проверки\n"
        "☐ Оплатить услуги гаранта (если на вашей стороне)\n"
        "☐ Принять второй аккаунт и подтвердить корректность\n"
        "☐ Подтвердить завершение обмена\n\n"
        "⚠️ Обмен с передачей Gmail не проводится.\n"
        "🔐 Конфиденциальные данные отправляйте только гаранту кнопкой ниже."
    )
    seller_text = (
        "🧾 <b>Чек‑лист обмена (продавец)</b>\n"
        "☐ Отправить гаранту ID аккаунта\n"
        "☐ Отправить скриншоты и данные для проверки\n"
        "☐ Оплатить услуги гаранта (если на вашей стороне)\n"
        "☐ Передать первый аккаунт гаранту (почта или перепривязка)\n"
        "☐ Подтвердить завершение обмена\n\n"
        "⚠️ Обмен с передачей Gmail не проводится.\n"
        "🔐 Конфиденциальные данные отправляйте только гаранту кнопкой ниже."
    )
    guarantor_text = (
        "🧾 <b>Чек‑лист обмена (гарант)</b>\n"
        "☐ Получить ID, скрины и данные обоих аккаунтов\n"
        "☐ Проверить соответствие договоренностям\n"
        "☐ Принять оплату услуги гаранта\n"
        "☐ Принять первый аккаунт и проверить доступ\n"
        "☐ Передать второй аккаунт второй стороне\n"
        "☐ После подтверждения передать первый аккаунт\n\n"
        "⚠️ Обмен с передачей Gmail не проводится.\n"
        "⚠️ Если первый аккаунт передан на почту гаранта, "
        "передавайте аккаунт вместе с этой почтой без перепривязки."
    )
    return buyer_text, seller_text, guarantor_text


@router.callback_query(
    F.data.startswith("buy:")
    | F.data.startswith("contact:")
    | F.data.startswith("exchange:")
)
async def start_deal(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    state: FSMContext,
) -> None:
    """Handle start deal.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
        state: Value for state.
    """
    action, raw_id = callback.data.split(":")
    ad_id = int(raw_id)

    async with sessionmaker() as session:
        buyer = await get_or_create_user(session, callback.from_user)
        result = await session.execute(
            select(Ad, Game, User)
            .join(Game, Game.id == Ad.game_id)
            .join(User, User.id == Ad.seller_id)
            .where(
                Ad.id == ad_id,
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
            )
        )
        row = result.first()
        if not row:
            await callback.answer("Объявление не найдено.")
            return

        ad, game, seller = row
        trust_score = await get_trust_score(session, seller.id)
        if seller.id == buyer.id:
            await callback.answer("Нельзя открыть сделку со своим объявлением.")
            return

        if action == "contact":
            await state.clear()
            await state.set_state(PreChatStates.in_chat)
            await state.update_data(
                ad_id=ad.id,
                peer_id=seller.id,
                role="buyer",
                ad_kind=ad.ad_kind,
            )
            await callback.message.answer(
                "💬 Диалог с продавцом открыт. Обсудите условия и цену.",
                reply_markup=prechat_finish_kb(ad.id),
            )
            await callback.bot.send_message(
                seller.id,
                (
                    "💬 Покупатель хочет связаться по вашему объявлению.\n"
                    "Нажмите кнопку ниже, чтобы открыть диалог."
                ),
                reply_markup=contact_open_kb(ad.id, buyer.id),
            )
            await callback.answer()
            return

        if action == "exchange":
            await state.clear()
            await state.update_data(ad_id=ad.id)
            await state.set_state(ExchangeStates.addon)
            await callback.message.answer(
                "💰 Укажите доплату в ₽. Если без доплаты — 0."
            )
            await callback.answer()
            return

        deal_type = "buy"
        fee = calculate_fee(ad.price, deal_type, trust_score=trust_score)
        if free_fee_active(seller.free_fee_until):
            fee = Decimal("0")
        deal = Deal(
            ad_id=ad.id,
            buyer_id=buyer.id,
            seller_id=seller.id,
            deal_type=deal_type,
            price=ad.price,
            fee=Decimal(fee) if fee is not None else None,
        )
        session.add(deal)
        await session.commit()

        admin_text = (
            f"Новая сделка #{deal.id}\n"
            f"Тип: {deal.deal_type}\n"
            f"Игра: {game.name}\n"
            f"Товар: {ad.title}\n"
            f"Цена: {ad.price} руб.\n"
            f"Комиссия: {deal.fee or 0} руб.\n"
            f"Покупатель: {await _format_user(buyer)}\n"
            f"Продавец: {await _format_user(seller)}"
        )
        await _send_admin_deal(callback.bot, settings, admin_text, deal.id)

    await callback.message.answer(
        f"✅ Заявка на сделку #{deal.id} отправлена. Ожидайте гаранта."
    )
    await callback.bot.send_message(
        seller.id,
        f"🧾 Поступила заявка на сделку #{deal.id}. Ожидайте гаранта.",
    )
    await callback.answer("✅ Заявка отправлена.")


@router.callback_query(F.data.startswith("prechat_open:"))
async def prechat_open(
    callback: CallbackQuery, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle prechat open.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    _, ad_id_raw, buyer_id_raw = callback.data.split(":")
    ad_id = int(ad_id_raw)
    buyer_id = int(buyer_id_raw)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Ad, User)
            .join(User, User.id == Ad.seller_id)
            .where(
                Ad.id == ad_id,
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
            )
        )
        row = result.first()
        if not row:
            await callback.answer("Объявление не найдено.")
            return
        ad, seller = row
        if seller.id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        if buyer_id == seller.id:
            await callback.answer("Неверный покупатель.")
            return

    await state.clear()
    await state.set_state(PreChatStates.in_chat)
    await state.update_data(
        ad_id=ad_id,
        peer_id=buyer_id,
        role="seller",
    )
    await callback.message.answer(
        "💬 Диалог открыт. Обсудите детали сделки. Для выхода — /exit."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prechat_finish:"))
async def prechat_finish(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle prechat finish.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    data = await state.get_data()
    if data.get("role") != "buyer":
        await callback.answer("Доступно только покупателю.")
        return
    ad_id = int(callback.data.split(":")[1])
    ad_kind = data.get("ad_kind")
    await callback.message.answer(
        "⚙️ Завершить диалог. Выберите действие:",
        reply_markup=prechat_action_kb(ad_id, is_exchange=ad_kind == "exchange"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prechat_cancel:"))
async def prechat_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle prechat cancel.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    data = await state.get_data()
    peer_id = data.get("peer_id")
    await state.clear()
    await callback.message.answer("✅ Диалог завершен.")
    if peer_id:
        await callback.bot.send_message(peer_id, "Покупатель завершил диалог.")
    await callback.answer()


@router.callback_query(F.data.startswith("prechat_buy:"))
async def prechat_buy(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle prechat buy.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    data = await state.get_data()
    if data.get("role") != "buyer":
        await callback.answer("Доступно только покупателю.")
        return
    if data.get("ad_kind") == "exchange":
        await callback.answer("Для обмена выберите «Обменять».")
        return
    ad_id = int(callback.data.split(":")[1])
    await state.set_state(PreChatStates.buy_price)
    await state.update_data(ad_id=ad_id)
    await callback.message.answer("💰 Введите согласованную цену (₽):")
    await callback.answer()


@router.callback_query(F.data.startswith("prechat_exchange:"))
async def prechat_exchange(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle prechat exchange.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    data = await state.get_data()
    if data.get("role") != "buyer":
        await callback.answer("Доступно только покупателю.")
        return
    ad_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(ad_id=ad_id)
    await state.set_state(ExchangeStates.addon)
    await callback.message.answer("🔁 Укажите доплату в ₽. Если без доплаты - 0.")
    await callback.answer()


@router.message(PreChatStates.in_chat)
async def prechat_relay(message: Message, state: FSMContext) -> None:
    """Handle prechat relay.

    Args:
        message: Value for message.
        state: Value for state.
    """
    data = await state.get_data()
    peer_id = data.get("peer_id")
    if not peer_id:
        await state.clear()
        await message.answer("⏱️ Диалог завершен.")
        return

    if message.text and message.text.strip() == "/exit":
        await state.clear()
        await message.answer("✅ Диалог завершен.")
        return

    if message.photo or message.video or message.document:
        await message.answer("Поддерживаются только текстовые сообщения.")
        return

    if message.text and contains_prohibited(message.text):
        await message.answer(
            "⛔ Контакты и ссылки запрещены. Используйте чат внутри GSNS."
        )
        async with sessionmaker() as session:
            await apply_trust_event(
                session,
                message.from_user.id,
                "guarantee_bypass",
                -7,
                "????? ???????",
                ref_type="prechat",
                ref_id=message.from_user.id,
                allow_duplicate=True,
            )
        return

    if message.text:
        await message.bot.send_message(peer_id, message.text)


@router.message(PreChatStates.buy_price)
async def prechat_buy_price(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle prechat buy price.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    ad_id = data.get("ad_id")
    peer_id = data.get("peer_id")
    if not ad_id:
        await state.clear()
        await message.answer("⏱️ Сессия истекла.")
        return
    try:
        price = Decimal((message.text or "").replace(",", "."))
        if price <= 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        await message.answer("Некорректная цена. Пример: 1500.")
        return

    async with sessionmaker() as session:
        result = await session.execute(
            select(Ad, User)
            .join(User, User.id == Ad.seller_id)
            .where(
                Ad.id == ad_id,
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
            )
        )
        row = result.first()
        if not row:
            await state.clear()
            await message.answer("Объявление не найдено или снято с публикации.")
            return
        ad, seller = row
        if peer_id and seller.id != peer_id:
            await state.clear()
            await message.answer("⛔ Диалог завершен.")
            return

    price_cents = _price_to_cents(price)
    await message.bot.send_message(
        seller.id,
        (
            "🧾 Запрос покупки по объявлению.\n"
            f"💰 Цена: {price} ₽\n"
            "Подтвердите или измените цену."
        ),
        reply_markup=seller_price_kb(ad.id, message.from_user.id, price_cents),
    )
    await state.clear()
    await message.answer("✅ Запрос отправлен продавцу. Ожидайте подтверждение.")


@router.callback_query(F.data.startswith("buy_confirm:"))
async def buy_confirm(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle buy confirm.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    _, ad_id_raw, buyer_id_raw, price_cents_raw = callback.data.split(":")
    ad_id = int(ad_id_raw)
    buyer_id = int(buyer_id_raw)
    price = _cents_to_price(int(price_cents_raw))

    async with sessionmaker() as session:
        seller = await get_or_create_user(session, callback.from_user)
        result = await session.execute(
            select(Ad, Game, User)
            .join(Game, Game.id == Ad.game_id)
            .join(User, User.id == Ad.seller_id)
            .where(
                Ad.id == ad_id,
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
            )
        )
        row = result.first()
        if not row:
            await callback.answer("Объявление не найдено.")
            return
        ad, game, ad_seller = row
        trust_score = await get_trust_score(session, seller.id)
        if ad_seller.id != seller.id:
            await callback.answer("Нет доступа.")
            return
        result = await session.execute(select(User).where(User.id == buyer_id))
        buyer = result.scalar_one_or_none()
        if not buyer or buyer.id == seller.id:
            await callback.answer("Покупатель не найден.")
            return

        fee = calculate_fee(price, "buy", trust_score=trust_score)
        if free_fee_active(seller.free_fee_until):
            fee = Decimal("0")
        deal = Deal(
            ad_id=ad.id,
            buyer_id=buyer_id,
            seller_id=seller.id,
            deal_type="buy",
            price=price,
            fee=Decimal(fee) if fee is not None else None,
        )
        session.add(deal)
        await session.commit()

        admin_text = (
            f"Новая сделка #{deal.id}\n"
            f"Тип: {deal.deal_type}\n"
            f"Игра: {game.name}\n"
            f"Товар: {ad.title}\n"
            f"Цена: {deal.price} руб.\n"
            f"Комиссия: {deal.fee or 0} руб.\n"
            f"Покупатель: {await _format_user(buyer)}\n"
            f"Продавец: {await _format_user(seller)}"
        )
        await _send_admin_deal(callback.bot, settings, admin_text, deal.id)

    await callback.bot.send_message(
        buyer_id,
        f"✅ Продавец подтвердил цену. Заявка #{deal.id} создана. Ожидайте гаранта.",
    )
    await callback.message.answer(f"✅ Заявка #{deal.id} создана. Ожидайте гаранта.")
    await callback.answer()


@router.callback_query(F.data.startswith("buy_change:"))
async def buy_change(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle buy change.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    _, ad_id_raw, buyer_id_raw = callback.data.split(":")
    await state.set_state(SellerPriceStates.change_price)
    await state.update_data(ad_id=int(ad_id_raw), buyer_id=int(buyer_id_raw))
    await callback.message.answer("💰 Введите новую цену (₽):")
    await callback.answer()


@router.callback_query(F.data.startswith("buy_cancel:"))
async def buy_cancel(callback: CallbackQuery) -> None:
    """Handle buy cancel.

    Args:
        callback: Value for callback.
    """
    _, _, buyer_id_raw = callback.data.split(":")
    buyer_id = int(buyer_id_raw)
    await callback.bot.send_message(buyer_id, "❌ Продавец отменил запрос.")
    await callback.message.answer("❌ Запрос отменен.")
    await callback.answer()


@router.message(SellerPriceStates.change_price)
async def buy_change_price(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle buy change price.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    data = await state.get_data()
    ad_id = data.get("ad_id")
    buyer_id = data.get("buyer_id")
    if not ad_id or not buyer_id:
        await state.clear()
        await message.answer("⏱️ Сессия истекла.")
        return
    try:
        price = Decimal((message.text or "").replace(",", "."))
        if price <= 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        await message.answer("Некорректная цена. Пример: 1500.")
        return

    async with sessionmaker() as session:
        seller = await get_or_create_user(session, message.from_user)
        result = await session.execute(
            select(Ad, Game, User)
            .join(Game, Game.id == Ad.game_id)
            .join(User, User.id == Ad.seller_id)
            .where(
                Ad.id == ad_id,
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
            )
        )
        row = result.first()
        if not row:
            await state.clear()
            await message.answer("Объявление не найдено.")
            return
        ad, game, ad_seller = row
        trust_score = await get_trust_score(session, seller.id)
        if ad_seller.id != seller.id:
            await state.clear()
            await message.answer("Нет доступа.")
            return
        result = await session.execute(select(User).where(User.id == buyer_id))
        buyer = result.scalar_one_or_none()
        if not buyer or buyer.id == seller.id:
            await state.clear()
            await message.answer("Покупатель не найден.")
            return

        fee = calculate_fee(price, "buy", trust_score=trust_score)
        if free_fee_active(seller.free_fee_until):
            fee = Decimal("0")
        deal = Deal(
            ad_id=ad.id,
            buyer_id=buyer_id,
            seller_id=seller.id,
            deal_type="buy",
            price=price,
            fee=Decimal(fee) if fee is not None else None,
        )
        session.add(deal)
        await session.commit()

        admin_text = (
            f"Новая сделка #{deal.id}\n"
            f"Тип: {deal.deal_type}\n"
            f"Игра: {game.name}\n"
            f"Товар: {ad.title}\n"
            f"Цена: {deal.price} руб.\n"
            f"Комиссия: {deal.fee or 0} руб.\n"
            f"Покупатель: {await _format_user(buyer)}\n"
            f"Продавец: {await _format_user(seller)}"
        )
        await _send_admin_deal(message.bot, settings, admin_text, deal.id)

    await state.clear()
    await message.bot.send_message(
        buyer_id,
        (
            "✅ Продавец изменил цену и подтвердил сделку.\n"
            f"Новая цена: {price} ₽. Заявка #{deal.id} создана."
        ),
    )
    await message.answer(f"✅ Заявка #{deal.id} создана. Ожидайте гаранта.")


@router.message(ExchangeStates.addon)
async def exchange_addon(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle exchange addon.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    try:
        addon_amount = Decimal((message.text or "").replace(",", "."))
        if addon_amount < 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        await message.answer("Укажите сумму доплаты числом (например: 0 или 1500).")
        return
    await state.update_data(addon_amount=addon_amount)
    await state.set_state(ExchangeStates.description)
    await message.answer(
        "Опишите, что вы отдаете взамен: что за аккаунт, состояние, доступы "
        "и важные детали."
    )


@router.message(ExchangeStates.description)
async def exchange_description(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle exchange description.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    description = (message.text or "").strip()
    if not description:
        await message.answer("Опишите условия обмена.")
        return
    if contains_prohibited(description):
        await message.answer(
            "Нельзя отправлять ссылки, юзернеймы и контакты вне GSNS. "
            "Опишите условия обмена без внешних контактов."
        )
        return

    data = await state.get_data()
    ad_id = data.get("ad_id")
    addon_amount = data.get("addon_amount") or Decimal("0")
    if not ad_id:
        await state.clear()
        await message.answer("⏱️ Сессия истекла. Попробуйте снова.")
        return

    async with sessionmaker() as session:
        buyer = await get_or_create_user(session, message.from_user)
        result = await session.execute(
            select(Ad, Game, User)
            .join(Game, Game.id == Ad.game_id)
            .join(User, User.id == Ad.seller_id)
            .where(
                Ad.id == ad_id,
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
            )
        )
        row = result.first()
        if not row:
            await state.clear()
            await message.answer("Объявление не найдено или снято с публикации.")
            return
        ad, game, seller = row
        trust_score = await get_trust_score(session, seller.id)
        if seller.id == buyer.id:
            await state.clear()
            await message.answer("Нельзя открыть сделку со своим объявлением.")
            return

        deal_type = "exchange_with_addon" if addon_amount > 0 else "exchange"
        fee = calculate_fee(
            addon_amount, deal_type, addon_amount, trust_score=trust_score
        )
        if free_fee_active(seller.free_fee_until):
            fee = Decimal("0")

        deal = Deal(
            ad_id=ad.id,
            buyer_id=buyer.id,
            seller_id=seller.id,
            deal_type=deal_type,
            price=addon_amount,
            fee=Decimal(fee) if fee is not None else None,
        )
        session.add(deal)
        await session.commit()

        addon_text = f"Доплата: {addon_amount} руб.\n" if addon_amount > 0 else ""
        seller_offer = f"{ad.title}\n{ad.description}".strip()
        buyer_offer = description
        admin_text = (
            f"Новая сделка #{deal.id}\n"
            f"Тип: {deal.deal_type}\n"
            f"Игра: {game.name}\n"
            f"Товар: {ad.title}\n"
            f"{addon_text}"
            f"Комиссия: {deal.fee or 0} руб.\n"
            f"Покупатель: {await _format_user(buyer)}\n"
            f"Продавец: {await _format_user(seller)}\n"
            f"Что отдает продавец:\n{seller_offer}\n"
            f"Что отдает покупатель:\n{buyer_offer}"
        )
        await _send_admin_deal(message.bot, settings, admin_text, deal.id)

    await state.clear()
    await message.answer(f"✅ Заявка на обмен #{deal.id} отправлена. Ожидайте гаранта.")
    await message.bot.send_message(
        seller.id,
        (
            f"🔁 Поступила заявка на обмен #{deal.id}.\n"
            "Проверьте описание и ожидайте гаранта."
        ),
    )


@router.callback_query(F.data.startswith("take:"))
async def take_deal(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle take deal.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    deal_id = int(callback.data.split(":")[1])

    async with sessionmaker() as session:
        guarantor = await get_or_create_user(session, callback.from_user)
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await callback.answer("Сделка не найдена.")
            return
        if deal.guarantee_id:
            await callback.answer("Сделка уже принята.")
            return

        if guarantor.role != "guarantor":
            await callback.answer("Нет доступа.")
            return
        if not guarantor.on_shift:
            await callback.answer("Вы не на смене.")
            return

        deal.guarantee_id = guarantor.id
        deal.status = "in_progress"
        await session.commit()

    guarantor_label = await _format_user(guarantor)
    buyer_markup = deal_after_take_kb(deal.id, role="buyer")
    seller_markup = deal_after_take_kb(deal.id, role="seller")
    guarantor_markup = deal_after_take_kb(deal.id, role="guarantor")

    await callback.bot.send_message(
        deal.buyer_id,
        (
            f"🛡️ Гарант {guarantor_label} подключился к сделке #{deal.id}.\n"
            "Откройте чат и передайте данные и оплату гаранту."
        ),
        reply_markup=buyer_markup,
    )
    await callback.bot.send_message(
        deal.seller_id,
        (
            f"🛡️ Гарант {guarantor_label} подключился к сделке #{deal.id}.\n"
            "Откройте чат и передайте данные гаранту."
        ),
        reply_markup=seller_markup,
    )
    await callback.bot.send_message(
        guarantor.id,
        f"✅ Вы назначены гарантом сделки #{deal.id}.",
        reply_markup=guarantor_markup,
    )

    if deal.deal_type in {"exchange", "exchange_with_addon"}:
        buyer_text, seller_text, guarantor_text = _exchange_checklists()
        await callback.bot.send_message(
            deal.buyer_id,
            buyer_text,
            reply_markup=buyer_markup,
        )
        await callback.bot.send_message(
            deal.seller_id,
            seller_text,
            reply_markup=seller_markup,
        )
        await callback.bot.send_message(guarantor.id, guarantor_text)

    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n✅ Сделку взял: {guarantor_label}",
            reply_markup=None,
        )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    await callback.answer("Сделка назначена на вас.")


@router.callback_query(F.data.startswith("chat:"))
async def open_chat(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle open chat.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    deal_id = int(callback.data.split(":")[1])

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await callback.answer("Сделка не найдена.")
            return

    role = None
    if callback.from_user.id == deal.buyer_id:
        role = "buyer"
    elif callback.from_user.id == deal.seller_id:
        role = "seller"
    elif callback.from_user.id == deal.guarantee_id:
        role = "guarantor"

    if role is None:
        await callback.answer("Нет доступа.")
        return
    if not deal.guarantee_id:
        await callback.answer("Ожидайте гаранта.")
        return

    await state.set_state(ChatStates.in_chat)
    await state.update_data(deal_id=deal_id, role=role)
    await callback.message.answer(
        f"💬 Чат по сделке #{deal_id} открыт.\n"
        "Не передавайте данные и оплату в общий чат — используйте кнопки.\n"
        "Для выхода — /exit."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal_data:"))
async def deal_data_start(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle deal data start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or not deal.guarantee_id:
            await callback.answer("Сделка не найдена.")
            return
        if callback.from_user.id not in {deal.buyer_id, deal.seller_id}:
            await callback.answer("Нет доступа.")
            return

    await state.set_state(DealSendStates.data)
    await state.update_data(deal_id=deal_id)
    await callback.message.answer("🔐 Отправьте данные гаранту.")
    await callback.answer()


@router.callback_query(F.data.startswith("deal_payment:"))
async def deal_payment_start(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle deal payment start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or not deal.guarantee_id:
            await callback.answer("Сделка не найдена.")
            return
        if callback.from_user.id not in {deal.buyer_id, deal.seller_id}:
            await callback.answer("Нет доступа.")
            return

    await state.set_state(DealSendStates.payment)
    await state.update_data(deal_id=deal_id)
    await callback.message.answer("💸 Отправьте информацию об оплате гаранту.")
    await callback.answer()


@router.message(DealSendStates.data)
async def deal_data_send(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle deal data send.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    deal_id = data.get("deal_id")
    if not deal_id:
        await state.clear()
        await message.answer("⏱️ Сессия истекла.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or not deal.guarantee_id:
            await state.clear()
            await message.answer("Сделка не найдена.")
            return
        if message.from_user.id not in {deal.buyer_id, deal.seller_id}:
            await state.clear()
            await message.answer("Нет доступа.")
            return

    prefix = f"{role_label('seller' if message.from_user.id == deal.seller_id else 'buyer')}:"
    if message.photo:
        await message.bot.send_photo(
            deal.guarantee_id,
            message.photo[-1].file_id,
            caption=f"{prefix} [данные]",
        )
    elif message.video:
        await message.bot.send_video(
            deal.guarantee_id,
            message.video.file_id,
            caption=f"{prefix} [данные]",
        )
    elif message.document:
        await message.bot.send_document(
            deal.guarantee_id,
            message.document.file_id,
            caption=f"{prefix} [данные]",
        )
    else:
        await message.bot.send_message(
            deal.guarantee_id,
            f"{prefix} {message.text or ''}",
        )

    await state.clear()
    await message.answer("✅ Данные отправлены гаранту.")


@router.message(DealSendStates.payment)
async def deal_payment_send(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle deal payment send.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    deal_id = data.get("deal_id")
    if not deal_id:
        await state.clear()
        await message.answer("⏱️ Сессия истекла.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or not deal.guarantee_id:
            await state.clear()
            await message.answer("Сделка не найдена.")
            return
        if message.from_user.id not in {deal.buyer_id, deal.seller_id}:
            await state.clear()
            await message.answer("Нет доступа.")
            return

    prefix = f"{role_label('seller' if message.from_user.id == deal.seller_id else 'buyer')}:"
    if message.photo:
        await message.bot.send_photo(
            deal.guarantee_id,
            message.photo[-1].file_id,
            caption=f"{prefix} [оплата]",
        )
    elif message.video:
        await message.bot.send_video(
            deal.guarantee_id,
            message.video.file_id,
            caption=f"{prefix} [оплата]",
        )
    elif message.document:
        await message.bot.send_document(
            deal.guarantee_id,
            message.document.file_id,
            caption=f"{prefix} [оплата]",
        )
    else:
        await message.bot.send_message(
            deal.guarantee_id,
            f"{prefix} {message.text or ''}",
        )

    await state.clear()
    await message.answer("✅ Оплата отправлена гаранту.")


@router.callback_query(F.data.startswith("dispute:"))
async def dispute_start(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle dispute start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await callback.answer("Сделка не найдена.")
            return
        if callback.from_user.id not in {
            deal.buyer_id,
            deal.seller_id,
            deal.guarantee_id,
        }:
            await callback.answer("Нет доступа.")
            return
        if not deal.guarantee_id:
            await callback.answer("Спор доступен после назначения гаранта.")
            return
        if deal.status in {"closed", "canceled"}:
            await callback.answer("Нельзя открыть спор по завершенной сделке.")
            return
        result = await session.execute(
            select(Dispute).where(
                Dispute.deal_id == deal.id,
                Dispute.status == "open",
            )
        )
        if result.scalar_one_or_none():
            await callback.answer("Спор уже открыт.")
            return

    await state.update_data(deal_id=deal_id)
    await callback.message.answer(
        f"Открыть спор по сделке #{deal_id}? Подтвердите действие.",
        reply_markup=confirm_action_kb("deal_dispute", deal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal_dispute_yes:"))
async def dispute_confirm_yes(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle dispute confirm yes.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    deal_id = int(callback.data.split(":")[1])
    await state.set_state(DisputeStates.reason)
    await state.update_data(deal_id=deal_id)
    await callback.message.answer("⚠️ Опишите причину спора.")
    await callback.answer()


@router.callback_query(F.data.startswith("deal_dispute_no:"))
async def dispute_confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle dispute confirm no.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await callback.message.answer("❌ Спор отменен.")
    await callback.answer()


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
        await message.answer("⏱️ Сессия истекла.")
        return

    async with sessionmaker() as session:
        dispute = Dispute(
            deal_id=deal_id,
            reporter_id=message.from_user.id,
            description=(message.text or "").strip(),
        )
        session.add(dispute)
        await session.commit()

    chat_id, topic_id = get_admin_target(settings)
    if chat_id != 0:
        await message.bot.send_message(
            chat_id,
            (
                f"Спор #{dispute.id} по сделке #{deal_id}\n"
                f"Инициатор: {message.from_user.id}\n"
                f"Причина: {dispute.description}"
            ),
            message_thread_id=topic_id,
        )

    await state.clear()
    await message.answer("✅ Спор отправлен. Ожидайте ответа в личных сообщениях.")


@router.message(ChatStates.in_chat)
async def relay_chat(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle relay chat.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    deal_id = data.get("deal_id")
    role = data.get("role")
    if not message.text:
        await message.answer("Сейчас поддерживаются только текстовые сообщения.")
        return

    if contains_prohibited(message.text):
        await message.answer(
            "Нельзя отправлять ссылки, юзернеймы и контакты вне GSNS. "
            "Используйте чат сделки внутри бота."
        )
        async with sessionmaker() as session:
            await apply_trust_event(
                session,
                message.from_user.id,
                "guarantee_bypass",
                -7,
                "????? ???????",
                ref_type="deal_chat",
                ref_id=message.from_user.id,
                allow_duplicate=True,
            )
        return

    if message.text.strip() == "/exit":
        await state.clear()
        await message.answer("Вы вышли из чата сделки.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await message.answer("Сделка не найдена.")
            await state.clear()
            return
        if role == "buyer" and message.from_user.id != deal.buyer_id:
            await message.answer("Нет доступа.")
            await state.clear()
            return
        if role == "seller" and message.from_user.id != deal.seller_id:
            await message.answer("Нет доступа.")
            await state.clear()
            return
        if role == "guarantor" and message.from_user.id != deal.guarantee_id:
            await message.answer("Нет доступа.")
            await state.clear()
            return

    if role == "buyer":
        target_ids = [deal.seller_id]
    elif role == "seller":
        target_ids = [deal.buyer_id]
    else:
        await message.answer(
            "Гаранту нужно указать адресата: /buyer текст или /seller текст."
        )
        return

    if deal.guarantee_id:
        target_ids.append(deal.guarantee_id)

    prefix = f"{role_label(role)}:"
    for target_id in target_ids:
        await message.bot.send_message(target_id, f"{prefix} {message.text}")


@router.message(F.text.startswith("/buyer ") | F.text.startswith("/seller "))
async def guarantor_message(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle guarantor message.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    if data.get("role") != "guarantor":
        return

    deal_id = data.get("deal_id")
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await message.answer("Сделка не найдена.")
            return
        if deal.guarantee_id != message.from_user.id:
            await message.answer("Нет доступа.")
            return

    if message.text.startswith("/buyer "):
        target_id = deal.buyer_id
    else:
        target_id = deal.seller_id

    content = message.text.split(" ", 1)[1]
    if contains_prohibited(content):
        await message.answer(
            "Нельзя отправлять ссылки, юзернеймы и контакты вне GSNS. "
            "Используйте чат сделки внутри бота."
        )
        return
    await message.bot.send_message(target_id, f"{role_label('guarantor')}: {content}")
