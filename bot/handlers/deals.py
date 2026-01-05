"""Module for deals functionality."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    Ad,
    Deal,
    DealMessage,
    DealRoom,
    Dispute,
    Game,
    Review,
    User,
)
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.ads import (
    admin_take_deal_kb,
    contact_open_kb,
    deal_after_take_kb,
    deal_room_guarantor_kb,
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
from bot.utils.roles import is_owner, is_staff
from bot.utils.vip import free_fee_active

router = Router()

_ROOM_SUMMARIES_POSTED: set[int] = set()


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


def _guarantor_prefix(tg_user) -> str:
    """Build a visible guarantor prefix for chat messages."""
    if tg_user.username:
        return f"Гарант @{tg_user.username}:"
    return f"Гарант id:{tg_user.id}:"


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


def _fmt_amount(value: Decimal | None) -> str:
    """Format currency with 2 decimals."""

    if not value:
        return "0"
    return f"{value.quantize(Decimal('0.01'))}"


def _deal_chat_list_kb(deals: list[Deal]) -> InlineKeyboardMarkup:
    """Build a keyboard with chat links for active deals."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"💬 Открыть чат #{deal.id} ({deal.status})",
                callback_data=f"chat:{deal.id}",
            )
        ]
        for deal in deals
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _deal_chat_menu_kb() -> ReplyKeyboardMarkup:
    """Build a quick menu for deal chat navigation."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/exit"), KeyboardButton(text="/deals")],
        ],
        resize_keyboard=True,
    )


def _build_deal_window_text(deal_id: int, role: str) -> str:
    """Build a deal chat window text with a stable marker."""
    role_name = role_label(role)
    return (
        f"Deal window #{deal_id}\n"
        f"Role: {role_name}\n"
        f"DEAL_ID:{deal_id}\n\n"
        "Reply to this message to send into the deal.\n"
        f"History: /deal_log {deal_id}"
    )


def _extract_deal_id(text: str | None) -> int | None:
    """Extract deal id from a window marker."""
    if not text:
        return None
    match = re.search(r"DEAL_ID:(\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def _extract_deal_id_from_reply(message: Message) -> int | None:
    """Extract deal id from a replied deal window message."""
    reply = message.reply_to_message
    if not reply or not reply.from_user or not reply.from_user.is_bot:
        return None
    return _extract_deal_id(reply.text or reply.caption)


async def _send_deal_window(message: Message, *, deal_id: int, role: str) -> None:
    """Send a deal window message for reply-based chat."""
    await message.answer(_build_deal_window_text(deal_id, role))


def _message_type_from_message(message: Message, *, base: str | None = None) -> str:
    """Resolve message type for logging."""
    if message.photo:
        media = "photo"
    elif message.video:
        media = "video"
    elif message.document:
        media = "document"
    else:
        media = "text"
    if base:
        return f"{base}_{media}" if media != "text" else base
    return media


async def _log_deal_message(
    sessionmaker: async_sessionmaker,
    *,
    deal_id: int,
    sender_id: int,
    sender_role: str,
    message_type: str,
    text: str | None = None,
    file_id: str | None = None,
) -> None:
    """Persist a deal message for recovery."""
    async with sessionmaker() as session:
        session.add(
            DealMessage(
                deal_id=deal_id,
                sender_id=sender_id,
                sender_role=sender_role,
                message_type=message_type,
                text=text,
                file_id=file_id,
            )
        )
        await session.commit()


def _is_room_member_status(status: str) -> bool:
    """Check if a chat member status means the user is inside the room."""
    return status in {"creator", "administrator", "member", "restricted"}


def _deal_room_invite_kb(invite_link: str) -> InlineKeyboardMarkup:
    """Build a button that opens the deal room invite link."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open deal chat", url=invite_link)]]
    )


async def _notify_room_pool_low(
    bot,
    settings: Settings,
    sessionmaker: async_sessionmaker,
) -> None:
    """Notify admin chat when free deal rooms are running low."""
    async with sessionmaker() as session:
        result = await session.execute(
            select(DealRoom).where(
                DealRoom.active.is_(True),
                DealRoom.assigned_deal_id.is_(None),
            )
        )
        free_rooms = result.scalars().all()

    if len(free_rooms) >= 3:
        return

    chat_id, topic_id = get_admin_target(settings)
    if chat_id == 0:
        return
    await bot.send_message(
        chat_id,
        f"Deal rooms running low: {len(free_rooms)} free rooms left.",
        message_thread_id=topic_id,
    )


async def _assign_deal_room(
    session,
    deal: Deal,
) -> tuple[DealRoom | None, str | None]:
    """Assign the first free room to a deal."""
    if deal.room_chat_id:
        result = await session.execute(
            select(DealRoom).where(DealRoom.chat_id == deal.room_chat_id)
        )
        room = result.scalar_one_or_none()
        return room, None

    result = await session.execute(
        select(DealRoom)
        .where(
            DealRoom.active.is_(True),
            DealRoom.assigned_deal_id.is_(None),
        )
        .order_by(DealRoom.id.asc())
    )
    room = result.scalars().first()
    if not room:
        return None, "No free deal rooms available."

    room.assigned_deal_id = deal.id
    deal.room_chat_id = room.chat_id
    deal.room_invite_link = room.invite_link
    deal.room_ready = False
    return room, None


async def _release_deal_room(session, deal: Deal) -> None:
    """Release room assignment after a deal completes or cancels."""

    if deal.room_chat_id:
        result = await session.execute(
            select(DealRoom).where(DealRoom.chat_id == deal.room_chat_id)
        )
        room = result.scalar_one_or_none()
        if room:
            room.assigned_deal_id = None
            room.invite_link = None
    deal.room_chat_id = None
    deal.room_invite_link = None
    deal.room_ready = False
    _ROOM_SUMMARIES_POSTED.discard(deal.id)


async def _mark_room_ready_and_notify(
    bot,
    sessionmaker: async_sessionmaker,
    *,
    deal_id: int,
    invite_link: str | None,
) -> None:
    """Mark deal room as ready and notify participants."""
    if not invite_link:
        return
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or deal.room_ready:
            return
        deal.room_ready = True
        if not deal.room_invite_link:
            deal.room_invite_link = invite_link
        await session.commit()

    text = f"Deal chat is ready for deal #{deal_id}:\n{invite_link}"
    await bot.send_message(
        deal.buyer_id,
        text,
        reply_markup=_deal_room_invite_kb(invite_link),
    )
    await bot.send_message(
        deal.seller_id,
        text,
        reply_markup=_deal_room_invite_kb(invite_link),
    )


async def _send_room_summary(
    bot,
    deal: Deal,
    chat_id: int,
    buyer_label: str,
    seller_label: str,
    guarantor_label: str,
) -> None:
    """Post summary about the deal once every participant has joined."""

    if deal.id in _ROOM_SUMMARIES_POSTED:
        return
    if not deal.guarantee_id:
        return

    price = _fmt_amount(deal.price)
    fee = _fmt_amount(deal.fee)
    lines = [
        f"👤 Гарант: {guarantor_label}",
        f"👤 Покупатель: {buyer_label}",
        f"👤 Продавец: {seller_label}",
    ]
    if deal.fee:
        lines.append(
            f"💰 Сумма сделки: {price} ₽ (аккаунт) + {fee} ₽ (гаранту)"
        )
    else:
        lines.append(f"💰 Сумма сделки: {price} ₽ (аккаунт)")

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Отзывы гаранта",
                    callback_data=f"guarantor_reviews:{deal.id}:{deal.guarantee_id}",
                )
            ]
        ]
    )

    await bot.send_message(chat_id, "\n".join(lines), reply_markup=markup)
    _ROOM_SUMMARIES_POSTED.add(deal.id)


async def _room_has_all_participants(bot, chat_id: int, deal: Deal) -> bool:
    """Check whether buyer, seller, and guarantor are present in the room."""

    if not deal.guarantee_id:
        return False

    for user_id in (deal.buyer_id, deal.seller_id, deal.guarantee_id):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramBadRequest:
            return False
        if not _is_room_member_status(member.status):
            return False
    return True


async def _prepare_room_for_deal(
    bot,
    sessionmaker: async_sessionmaker,
    deal: Deal,
) -> tuple[Deal | None, str | None]:
    """Ensure the deal has room and invite_link."""

    async with sessionmaker() as session:
        db_deal = await session.get(Deal, deal.id)
        if not db_deal:
            return None, None
        if not db_deal.room_chat_id:
            result = await session.execute(
                select(DealRoom).where(DealRoom.assigned_deal_id == db_deal.id)
            )
            room = result.scalar_one_or_none()
            if room:
                db_deal.room_chat_id = room.chat_id
                if room.invite_link and not db_deal.room_invite_link:
                    db_deal.room_invite_link = room.invite_link
                await session.commit()

    invite_link = None
    if db_deal.room_chat_id:
        async with sessionmaker() as session:
            result = await session.execute(
                select(DealRoom).where(DealRoom.chat_id == db_deal.room_chat_id)
            )
            room = result.scalar_one_or_none()
            if room:
                if not room.invite_link:
                    try:
                        invite = await bot.create_chat_invite_link(
                            db_deal.room_chat_id,
                            name="GSNS deal room",
                        )
                        room.invite_link = invite.invite_link
                        db_deal.room_invite_link = invite.invite_link
                        invite_link = invite.invite_link
                        await session.commit()
                    except Exception:
                        pass
                else:
                    invite_link = room.invite_link
    return db_deal, invite_link


async def _find_active_deal(
    sessionmaker: async_sessionmaker,
    user_id: int,
    *,
    deal_id: int | None = None,
) -> Deal | None:
    """Return the latest active deal for the user or specific id."""

    conditions = (
        Deal.status.not_in({"closed", "canceled"}),
        or_(
            Deal.buyer_id == user_id,
            Deal.seller_id == user_id,
            Deal.guarantee_id == user_id,
        ),
    )
    async with sessionmaker() as session:
        query = select(Deal).where(*conditions)
        if deal_id:
            query = query.where(Deal.id == deal_id)
        result = await session.execute(query.order_by(Deal.id.desc()).limit(1))
        return result.scalar_one_or_none()


async def _roles_summary(
    bot,
    deal: Deal,
    chat_id: int | None = None,
) -> list[str]:
    """Build the whois summary lines with member statuses."""

    lines: list[str] = []
    for label, user_id in (
        ("Гарант", deal.guarantee_id),
        ("Покупатель", deal.buyer_id),
        ("Продавец", deal.seller_id),
    ):
        if not user_id:
            lines.append(f"{label}: —")
            continue
        if not chat_id:
            status = "нет комнаты"
        else:
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                status = member.status
            except TelegramBadRequest:
                status = "не в чате"
            except Exception:
                status = "не в чате"
        lines.append(f"{label}: {status} ({user_id})")
    return lines


async def _send_deal_room_intro(
    bot,
    sessionmaker: async_sessionmaker,
    deal: Deal,
    role: str,
    chat_id: int,
) -> None:
    """Send deal details and role-specific buttons into the room chat."""

    if not await _room_has_all_participants(bot, chat_id, deal):
        return

    async with sessionmaker() as session:
        buyer = await session.get(User, deal.buyer_id)
        seller = await session.get(User, deal.seller_id)
        guarantor = (
            await session.get(User, deal.guarantee_id) if deal.guarantee_id else None
        )

    buyer_label = await _format_user(buyer) if buyer else "id:-"
    seller_label = await _format_user(seller) if seller else "id:-"
    guarantor_label = await _format_user(guarantor) if guarantor else "—"
    await _send_room_summary(
        bot,
        deal,
        chat_id,
        buyer_label,
        seller_label,
        guarantor_label,
    )
    lines = [
        "🤝 <b>Сделка</b>",
        f"ID: {deal.id}",
        f"Покупатель: {buyer_label}",
        f"Продавец: {seller_label}",
        f"Гарант: {guarantor_label}",
        f"Ваша роль: {role}",
    ]
    if role == "guarantor":
        lines.append("Гарант может завершить или отменить сделку кнопками ниже.")
        markup = deal_room_guarantor_kb(deal.id)
    else:
        lines.append("Кнопки в этом чате доступны только гаранту. Ждите инструкций.")
        markup = None

    await bot.send_message(
        chat_id,
        "\n".join(lines),
        reply_markup=markup,
        parse_mode="HTML",
    )


async def _resolve_deal_chat(
    sessionmaker: async_sessionmaker, deal_id: int, user_id: int
) -> tuple[Deal | None, str | None, str | None]:
    """Resolve deal and role for chat entry."""
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            return None, None, "Сделка не найдена."

    if deal.status in {"closed", "canceled"}:
        return None, None, "Сделка завершена или отменена."

    role = None
    if user_id == deal.buyer_id:
        role = "buyer"
    elif user_id == deal.seller_id:
        role = "seller"
    elif user_id == deal.guarantee_id:
        role = "guarantor"

    if role is None:
        return None, None, "Нет доступа."
    if not deal.guarantee_id:
        return None, None, "Ожидайте гаранта."
    return deal, role, None


async def _resolve_active_user_deal(
    sessionmaker: async_sessionmaker,
    user_id: int,
    *,
    deal_id: int | None = None,
) -> tuple[Deal | None, str | None, str | None]:
    """Resolve an active deal for the user, optionally by ID."""

    if deal_id:
        return await _resolve_deal_chat(sessionmaker, deal_id, user_id)

    deal = await _find_active_deal(sessionmaker, user_id)
    if not deal:
        return None, None, "Активных сделок нет."
    return await _resolve_deal_chat(sessionmaker, deal.id, user_id)


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
async def prechat_relay(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
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
                "Обход гаранта",
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
    room = None
    room_error = None

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
        room, room_error = await _assign_deal_room(session, deal)
        await session.commit()

    guarantor_label = await _format_user(guarantor)
    buyer_markup = deal_after_take_kb(
        deal.id,
        role="buyer",
        guarantor_id=guarantor.id,
    )
    seller_markup = deal_after_take_kb(
        deal.id,
        role="seller",
        guarantor_id=guarantor.id,
    )
    guarantor_markup = deal_after_take_kb(
        deal.id,
        role="guarantor",
        guarantor_id=guarantor.id,
    )

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

    if room_error:
        await callback.bot.send_message(
            guarantor.id,
            f"Deal #{deal.id} has no room yet. {room_error}",
        )
        chat_id, topic_id = get_admin_target(settings)
        if chat_id:
            await callback.bot.send_message(
                chat_id,
                f"Deal #{deal.id} taken, but no free rooms available.",
                message_thread_id=topic_id,
            )
    elif room and room.invite_link:
        await callback.bot.send_message(
            guarantor.id,
            (
                f"Deal #{deal.id} room assigned. "
                "Press “Open chat” to release the link to participants."
            ),
        )

    await _notify_room_pool_low(callback.bot, settings, sessionmaker)


@router.message(Command("deal_room_add"))
async def deal_room_add(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Register a deal room created by staff."""
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if not message.from_user:
        await message.answer("Отключите анонимность администратора и повторите.")
        return

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not is_staff(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await message.answer("Нет доступа.")
            return

    try:
        invite = await message.bot.create_chat_invite_link(
            message.chat.id,
            name="GSNS deal room",
        )
    except Exception:
        await message.answer("Cannot create invite link. Make sure the bot is admin.")
        return

    async with sessionmaker() as session:
        result = await session.execute(
            select(DealRoom).where(DealRoom.chat_id == message.chat.id)
        )
        room = result.scalar_one_or_none()
        if room:
            room.title = message.chat.title
            room.invite_link = invite.invite_link
            room.active = True
            room.created_by = user.id
        else:
            session.add(
                DealRoom(
                    chat_id=message.chat.id,
                    title=message.chat.title,
                    invite_link=invite.invite_link,
                    active=True,
                    created_by=user.id,
                )
            )
        await session.commit()

    await message.answer("Deal room registered.")
    await _notify_room_pool_low(message.bot, settings, sessionmaker)


@router.message(Command("deal_rooms_free"))
async def deal_rooms_free(
    message: Message,
    sessionmaker: async_sessionmaker,
) -> None:
    """List free deal rooms."""
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if not message.from_user:
        await message.answer("Отключите анонимность администратора и повторите.")
        return
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not is_staff(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await message.answer("Нет доступа.")
            return
        result = await session.execute(
            select(DealRoom).where(
                DealRoom.active.is_(True),
                DealRoom.assigned_deal_id.is_(None),
            )
        )
        rooms = result.scalars().all()

    if not rooms:
        await message.answer("No free deal rooms.")
        return

    lines = [f"Free deal rooms: {len(rooms)}"]
    for room in rooms[:50]:
        title = room.title or "-"
        invite = "yes" if room.invite_link else "no"
        lines.append(f"{room.id}. {title} (chat {room.chat_id}) invite:{invite}")
    await message.answer("\n".join(lines))


@router.message(Command("deal_room_status"))
async def deal_room_status(
    message: Message,
    sessionmaker: async_sessionmaker,
) -> None:
    """Show room status for a deal."""
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if not message.from_user:
        await message.answer("Отключите анонимность администратора и повторите.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /deal_room_status DEAL_ID")
        return
    deal_id = int(parts[1])

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not is_staff(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await message.answer("Нет доступа.")
            return
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await message.answer("Deal not found.")
            return
        room = None
        if deal.room_chat_id:
            result = await session.execute(
                select(DealRoom).where(DealRoom.chat_id == deal.room_chat_id)
            )
            room = result.scalar_one_or_none()
        if not room:
            result = await session.execute(
                select(DealRoom).where(DealRoom.assigned_deal_id == deal.id)
            )
            room = result.scalar_one_or_none()

    if not room:
        await message.answer("No room assigned to this deal.")
        return

    lines = [
        f"Deal #{deal.id}",
        f"Room chat: {room.chat_id}",
        f"Title: {room.title or '-'}",
        f"Active: {room.active}",
        f"Assigned: {room.assigned_deal_id}",
        f"Invite: {'yes' if room.invite_link else 'no'}",
        f"Ready: {deal.room_ready}",
    ]
    if room.invite_link:
        lines.append(room.invite_link)
    await message.answer("\n".join(lines))


@router.chat_member()
async def deal_room_member_update(
    event: ChatMemberUpdated, sessionmaker: async_sessionmaker
) -> None:
    """Send deal details when a participant joins the room."""
    if not event.new_chat_member or event.new_chat_member.user.is_bot:
        return
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    if _is_room_member_status(old_status) or not _is_room_member_status(new_status):
        return

    async with sessionmaker() as session:
        result = await session.execute(
            select(DealRoom).where(
                DealRoom.chat_id == event.chat.id,
                DealRoom.assigned_deal_id.is_not(None),
            )
        )
        room = result.scalar_one_or_none()
        if not room or not room.assigned_deal_id:
            return
        result = await session.execute(
            select(Deal).where(Deal.id == room.assigned_deal_id)
        )
        deal = result.scalar_one_or_none()

    if not deal:
        return
    role = None
    if event.new_chat_member.user.id == deal.buyer_id:
        role = "buyer"
    elif event.new_chat_member.user.id == deal.seller_id:
        role = "seller"
    elif event.new_chat_member.user.id == deal.guarantee_id:
        role = "guarantor"
    if not role:
        return
    await _send_deal_room_intro(
        event.bot,
        sessionmaker,
        deal=deal,
        role=role,
        chat_id=event.chat.id,
    )


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

    deal, role, error = await _resolve_deal_chat(
        sessionmaker, deal_id, callback.from_user.id
    )
    if error:
        await callback.answer(error)
        return

    deal, invite_link = await _prepare_room_for_deal(
        callback.bot, sessionmaker, deal
    )
    if not deal:
        await callback.answer("Сделка не найдена.")
        return
    if invite_link and deal.room_ready:
        await callback.message.answer(
            f"Deal chat is ready:\n{invite_link}",
            reply_markup=_deal_room_invite_kb(invite_link),
        )
        if deal.room_chat_id:
            try:
                await _send_deal_room_intro(
                    callback.bot,
                    sessionmaker,
                    deal=deal,
                    role=role,
                    chat_id=deal.room_chat_id,
                )
            except Exception:
                pass
        await callback.answer()
        return

    if not deal.room_chat_id:
        await callback.message.answer(
            "\u0427\u0430\u0442 \u0434\u043b\u044f \u0441\u0434\u0435\u043b\u043a\u0438 \u0435\u0449\u0435 \u043d\u0435 \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d."
        )
        await callback.answer()
        return

    invite_link = deal.room_invite_link
    async with sessionmaker() as session:
        result = await session.execute(
            select(DealRoom).where(DealRoom.chat_id == deal.room_chat_id)
        )
        room = result.scalar_one_or_none()
        if room and not room.invite_link:
            try:
                invite = await callback.bot.create_chat_invite_link(
                    deal.room_chat_id,
                    name="GSNS deal room",
                )
                room.invite_link = invite.invite_link
                deal.room_invite_link = invite.invite_link
                invite_link = invite.invite_link
                await session.commit()
            except Exception:
                pass
        elif room and room.invite_link:
            invite_link = room.invite_link

    if not invite_link:
        await callback.message.answer(
            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u043d\u0432\u0430\u0439\u0442 \u0432 \u043a\u043e\u043c\u043d\u0430\u0442\u0443. "
            "\u0411\u043e\u0442 \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u0430\u0434\u043c\u0438\u043d\u043e\u043c."
        )
        await callback.answer()
        return

    if role == "guarantor" and invite_link:
        await _mark_room_ready_and_notify(
            callback.bot,
            sessionmaker,
            deal_id=deal.id,
            invite_link=invite_link,
        )
        await _send_deal_room_intro(
            callback.bot,
            sessionmaker,
            deal=deal,
            role="guarantor",
            chat_id=deal.room_chat_id,
        )
        await callback.answer()
        return

    await callback.message.answer(
        "\u0427\u0430\u0442 \u0435\u0449\u0435 \u043d\u0435 \u0433\u043e\u0442\u043e\u0432. "
        "\u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c, \u043f\u043e\u043a\u0430 \u0433\u0430\u0440\u0430\u043d\u0442 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u0442\u0441\u044f \u043a \u043a\u043e\u043c\u043d\u0430\u0442\u0435."
    )
    await callback.answer()


@router.message(F.text == "/deals")
async def list_active_deals(message: Message, sessionmaker: async_sessionmaker) -> None:
    """List active deals for quick chat access."""
    async with sessionmaker() as session:
        result = await session.execute(
            select(Deal)
            .where(
                Deal.status.not_in({"closed", "canceled"}),
                (Deal.buyer_id == message.from_user.id)
                | (Deal.seller_id == message.from_user.id)
                | (Deal.guarantee_id == message.from_user.id),
            )
            .order_by(Deal.id.desc())
            .limit(20)
        )
        deals = result.scalars().all()

    if not deals:
        await message.answer("Активных сделок нет.")
        return

    await message.answer(
        "Ваши активные сделки. Нажмите, чтобы открыть чат:",
        reply_markup=_deal_chat_list_kb(deals),
    )


@router.message(F.text.startswith("/deal "))
async def switch_deal_chat(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Switch active chat to another deal."""
    await message.answer(
        "Deal chat inside the bot is disabled. Use the deal room link."
    )
    return


@router.message(F.text.startswith("/deal_log"))
async def deal_log(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Show deal message log."""
    parts = message.text.split() if message.text else []
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /deal_log ID [count]")
        return
    deal_id = int(parts[1])
    limit = 20
    if len(parts) > 2 and parts[2].isdigit():
        limit = max(1, min(int(parts[2]), 50))

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await message.answer("Deal not found.")
            return
        if message.from_user.id not in {
            deal.buyer_id,
            deal.seller_id,
            deal.guarantee_id,
        }:
            await message.answer("No access.")
            return
        result = await session.execute(
            select(DealMessage)
            .where(DealMessage.deal_id == deal_id)
            .order_by(DealMessage.id.desc())
            .limit(limit)
        )
        items = result.scalars().all()

    if not items:
        await message.answer("No messages yet.")
        return

    items.reverse()
    await message.answer(f"Deal #{deal_id} log (last {len(items)})")
    for item in items:
        ts = item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "-"
        role_name = role_label(item.sender_role)
        tag = ""
        if item.message_type.startswith("data"):
            tag = "DATA"
        elif item.message_type.startswith("payment"):
            tag = "PAYMENT"
        prefix = f"[{ts}] {role_name}{(' ' + tag) if tag else ''}"
        text = item.text or ""
        msg_type = item.message_type or "text"
        if "photo" in msg_type and item.file_id:
            await message.bot.send_photo(
                message.chat.id,
                item.file_id,
                caption=f"{prefix} {text}".strip(),
            )
        elif "video" in msg_type and item.file_id:
            await message.bot.send_video(
                message.chat.id,
                item.file_id,
                caption=f"{prefix} {text}".strip(),
            )
        elif "document" in msg_type and item.file_id:
            await message.bot.send_document(
                message.chat.id,
                item.file_id,
                caption=f"{prefix} {text}".strip(),
            )
        else:
            await message.answer(f"{prefix} {text}".strip())

    deal, role, error = await _resolve_deal_chat(
        sessionmaker, deal_id, message.from_user.id
    )
    if error:
        await message.answer(error)
        return

    await _send_deal_window(message, deal_id=deal_id, role=role)


@router.message(Command("chat"))
async def chat_command(
    message: Message,
    sessionmaker: async_sessionmaker,
) -> None:
    """Provide the deal room invite and trigger the room intro when ready."""

    if not message.from_user:
        return

    parts = (message.text or "").split()
    deal_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    deal, role, error = await _resolve_active_user_deal(
        sessionmaker, message.from_user.id, deal_id=deal_id
    )
    if error:
        await message.answer(error)
        return

    deal, invite_link = await _prepare_room_for_deal(
        message.bot, sessionmaker, deal
    )
    if not deal:
        await message.answer("Сделка не найдена.")
        return
    if not deal.room_chat_id:
        await message.answer("Чат для сделки ещё не назначен.")
        return
    if not invite_link:
        await message.answer(
            "Не удалось получить ссылку на чат сделки. Обратитесь к администратору."
        )
        return

    chat_id = deal.room_chat_id
    room_ready = await _room_has_all_participants(message.bot, chat_id, deal)
    status_text: str
    if room_ready:
        await _send_deal_room_intro(
            message.bot,
            sessionmaker,
            deal=deal,
            role=role,
            chat_id=chat_id,
        )
        status_text = "Инструкция отправлена в комнату после того, как все участники присутствуют."
    else:
        status_lines = await _roles_summary(message.bot, deal, chat_id)
        status_text = "Ждём участников:\n" + "\n".join(status_lines)

    lines = [
        f"Чат сделки #{deal.id}",
        f"Роль: {role_label(role)}",
        f"Ссылка: {invite_link}",
        "",
        status_text,
    ]
    await message.answer(
        "\n".join(lines),
        reply_markup=_deal_room_invite_kb(invite_link),
    )


@router.message(Command("whois"))
async def whois_command(
    message: Message,
    sessionmaker: async_sessionmaker,
) -> None:
    """Show participant statuses and review button for the current deal."""

    if not message.from_user:
        return

    parts = (message.text or "").split()
    deal_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    deal, _, error = await _resolve_active_user_deal(
        sessionmaker, message.from_user.id, deal_id=deal_id
    )
    if error:
        await message.answer(error)
        return

    chat_id = deal.room_chat_id
    status_lines = await _roles_summary(message.bot, deal, chat_id)

    lines = [
        f"Сделка #{deal.id} ({deal.status})",
        f"Комната: {chat_id or 'не назначена'}",
        f"Ссылка: {deal.room_invite_link or '—'}",
        "",
        *status_lines,
    ]

    markup = None
    if deal.guarantee_id:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡️ Отзывы гаранта",
                        callback_data=f"guarantor_reviews:{deal.id}:{deal.guarantee_id}",
                    )
                ]
            ]
        )

    await message.answer("\n".join(lines), reply_markup=markup)


@router.callback_query(F.data.startswith("guarantor_reviews:"))
async def guarantor_reviews(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
) -> None:
    """Show guarantor reviews for a deal participant."""
    _, deal_id_raw, guarantor_id_raw = callback.data.split(":")
    deal_id = int(deal_id_raw)
    guarantor_id = int(guarantor_id_raw)

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal or not deal.guarantee_id:
            await callback.answer("Сделка не найдена.")
            return
        if deal.guarantee_id != guarantor_id:
            await callback.answer("Нет доступа.")
            return
        if callback.from_user.id not in {
            deal.buyer_id,
            deal.seller_id,
            deal.guarantee_id,
        }:
            await callback.answer("Нет доступа.")
            return

        result = await session.execute(select(User).where(User.id == guarantor_id))
        guarantor = result.scalar_one_or_none()
        if not guarantor:
            await callback.answer("Гарант не найден.")
            return

        author = aliased(User)
        result = await session.execute(
            select(Review, author)
            .join(author, author.id == Review.author_id)
            .where(Review.target_id == guarantor_id, Review.status == "active")
            .order_by(Review.id.desc())
            .limit(20)
        )
        rows = result.all()

    rating_avg = float(guarantor.rating_avg or 0)
    rating_count = guarantor.rating_count or 0
    guarantor_label = await _format_user(guarantor)
    header = (
        f"⭐ Отзывы о гаранте {guarantor_label}\n"
        f"Рейтинг: {rating_avg:.2f} ({rating_count} отзывов)"
    )
    if not rows:
        await callback.message.answer(f"{header}\n\nОтзывов пока нет.")
        await callback.answer()
        return

    lines = [header, ""]
    for review, author_user in rows:
        author_label = await _format_user(author_user)
        comment = review.comment or "без комментария"
        lines.append(f"• {review.rating}/5 — {comment} (от {author_label})")

    await callback.message.answer("\n".join(lines))
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
        if deal.status in {"closed", "canceled"}:
            await callback.answer("Сделка завершена или отменена.")
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
        if deal.status in {"closed", "canceled"}:
            await callback.answer("Сделка завершена или отменена.")
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
        if deal.status in {"closed", "canceled"}:
            await state.clear()
            await message.answer("Сделка завершена или отменена.")
            return
        if message.from_user.id not in {deal.buyer_id, deal.seller_id}:
            await state.clear()
            await message.answer("Нет доступа.")
            return

    role_name = role_label(
        "seller" if message.from_user.id == deal.seller_id else "buyer"
    )
    role_key = "seller" if message.from_user.id == deal.seller_id else "buyer"
    message_type = _message_type_from_message(message, base="data")
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id

    await _log_deal_message(
        sessionmaker,
        deal_id=deal.id,
        sender_id=message.from_user.id,
        sender_role=role_key,
        message_type=message_type,
        text=message.text or message.caption,
        file_id=file_id,
    )

    header = "⚠️ <b>ДАННЫЕ ПО СДЕЛКЕ</b>\n" f"Сделка #{deal_id}\n" f"От: {role_name}"
    prefix = f"{role_name}:"
    if message.photo:
        await message.bot.send_message(deal.guarantee_id, header, parse_mode="HTML")
        await message.bot.send_photo(
            deal.guarantee_id,
            message.photo[-1].file_id,
            caption="📎 Данные",
        )
    elif message.video:
        await message.bot.send_message(deal.guarantee_id, header, parse_mode="HTML")
        await message.bot.send_video(
            deal.guarantee_id,
            message.video.file_id,
            caption="📎 Данные",
        )
    elif message.document:
        await message.bot.send_message(deal.guarantee_id, header, parse_mode="HTML")
        await message.bot.send_document(
            deal.guarantee_id,
            message.document.file_id,
            caption="📎 Данные",
        )
    else:
        await message.bot.send_message(
            deal.guarantee_id,
            f"{header}\n\n{message.text or ''}",
            parse_mode="HTML",
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
        if deal.status in {"closed", "canceled"}:
            await state.clear()
            await message.answer("Сделка завершена или отменена.")
            return
        if message.from_user.id not in {deal.buyer_id, deal.seller_id}:
            await state.clear()
            await message.answer("Нет доступа.")
            return

    role_name = role_label(
        "seller" if message.from_user.id == deal.seller_id else "buyer"
    )
    role_key = "seller" if message.from_user.id == deal.seller_id else "buyer"
    message_type = _message_type_from_message(message, base="payment")
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id

    await _log_deal_message(
        sessionmaker,
        deal_id=deal.id,
        sender_id=message.from_user.id,
        sender_role=role_key,
        message_type=message_type,
        text=message.text or message.caption,
        file_id=file_id,
    )

    header = "💸 <b>ОПЛАТА ПО СДЕЛКЕ</b>\n" f"Сделка #{deal_id}\n" f"От: {role_name}"
    prefix = f"{role_name}:"
    if message.photo:
        await message.bot.send_message(deal.guarantee_id, header, parse_mode="HTML")
        await message.bot.send_photo(
            deal.guarantee_id,
            message.photo[-1].file_id,
            caption="📎 Оплата",
        )
    elif message.video:
        await message.bot.send_message(deal.guarantee_id, header, parse_mode="HTML")
        await message.bot.send_video(
            deal.guarantee_id,
            message.video.file_id,
            caption="📎 Оплата",
        )
    elif message.document:
        await message.bot.send_message(deal.guarantee_id, header, parse_mode="HTML")
        await message.bot.send_document(
            deal.guarantee_id,
            message.document.file_id,
            caption="📎 Оплата",
        )
    else:
        await message.bot.send_message(
            deal.guarantee_id,
            f"{header}\n\n{message.text or ''}",
            parse_mode="HTML",
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
            await callback.answer("Сделка завершена или отменена.")
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


def _is_deal_window_reply(message: Message) -> bool:
    """Check if message replies to a deal window."""
    return _extract_deal_id_from_reply(message) is not None


async def _relay_deal_message(
    message: Message,
    sessionmaker: async_sessionmaker,
    deal: Deal,
    role: str,
) -> None:
    """Relay a message inside a deal chat."""
    if not message.text:
        if not (message.photo or message.video):
            await message.answer("????? ?????????? ?????? ?????, ???? ??? ?????.")
            return

    check_text = message.text or message.caption or ""
    if check_text and contains_prohibited(check_text):
        await message.answer(
            "? ???????? ? ?????? ?????????. ??????????? ??? ?????? GSNS."
        )
        async with sessionmaker() as session:
            await apply_trust_event(
                session,
                message.from_user.id,
                "guarantee_bypass",
                -7,
                "??????? ?????? ???????",
                ref_type="deal_chat",
                ref_id=message.from_user.id,
                allow_duplicate=True,
            )
        return

    message_type = _message_type_from_message(message)
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id

    await _log_deal_message(
        sessionmaker,
        deal_id=deal.id,
        sender_id=message.from_user.id,
        sender_role=role,
        message_type=message_type,
        text=message.text or message.caption,
        file_id=file_id,
    )

    if role == "buyer":
        target_ids = [deal.seller_id]
    elif role == "seller":
        target_ids = [deal.buyer_id]
    else:
        target_ids = [deal.buyer_id, deal.seller_id]

    if deal.guarantee_id and role in {"buyer", "seller"}:
        target_ids.append(deal.guarantee_id)

    if role == "guarantor":
        prefix = _guarantor_prefix(message.from_user)
    else:
        prefix = f"{role_label(role)}:"

    for target_id in target_ids:
        if message.photo:
            await message.bot.send_photo(
                target_id,
                message.photo[-1].file_id,
                caption=f"{prefix} {message.caption or ''}".strip(),
            )
        elif message.video:
            await message.bot.send_video(
                target_id,
                message.video.file_id,
                caption=f"{prefix} {message.caption or ''}".strip(),
            )
        else:
            await message.bot.send_message(target_id, f"{prefix} {message.text}")


@router.message(_is_deal_window_reply)
async def relay_chat_reply(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Relay a reply to a deal window without entering chat state."""
    await message.answer(
        "Deal chat inside the bot is disabled. Use the deal room link."
    )
    return


@router.message(ChatStates.in_chat)
async def relay_chat(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle relay chat for legacy state-based mode."""
    await state.clear()
    await message.answer(
        "Deal chat inside the bot is disabled. Use the deal room link.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return


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

    if deal.status in {"closed", "canceled"}:
        await message.answer("Сделка завершена или отменена.")
        return

    if message.text.startswith("/buyer "):
        target_id = deal.buyer_id
    else:
        target_id = deal.seller_id

    content = message.text.split(" ", 1)[1]
    await _log_deal_message(
        sessionmaker,
        deal_id=deal.id,
        sender_id=message.from_user.id,
        sender_role="guarantor",
        message_type="text",
        text=content,
    )

    if contains_prohibited(content):
        await message.answer(
            "Нельзя отправлять ссылки, юзернеймы и контакты вне GSNS. "
            "Используйте чат сделки внутри бота."
        )
        return
    prefix = _guarantor_prefix(message.from_user)
    await message.bot.send_message(target_id, f"{prefix} {content}")
