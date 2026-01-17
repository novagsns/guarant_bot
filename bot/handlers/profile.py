"""Module for profile functionality."""

from __future__ import annotations

from datetime import datetime, timedelta
import html
import re
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable

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
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    Ad,
    Deal,
    Game,
    Review,
    Service,
    ServicePurchase,
    User,
    WalletTransaction,
)
from bot.handlers.helpers import get_or_create_user
from bot.services.currency import (
    coins_per_rub_rate,
    rub_to_coins,
    rub_to_usdt,
    usdt_per_rub_rate,
)
from bot.services.trust import (
    apply_deal_no_dispute_bonus,
    apply_trust_event,
    get_trust_factors,
    get_trust_score,
)
from bot.keyboards.ads import game_list_kb
from bot.keyboards.common import REVIEW_MENU_BUTTON
from bot.keyboards.profile import (
    ad_edit_kb,
    deal_detail_kb,
    deal_list_kb,
    my_ad_manage_kb,
    profile_actions_kb,
    wallet_tx_kb,
)
from bot.keyboards.vip import vip_menu_kb
from bot.utils.broadcasts import create_broadcast_request
from bot.utils.vip import free_fee_active, is_vip_until

router = Router()

_profile_message_ids: dict[int, int] = {}
REVIEWS_PER_PAGE = 5


async def _cleanup_profile_message(user_id: int, bot) -> None:
    msg_id = _profile_message_ids.pop(user_id, None)
    if not msg_id:
        return
    try:
        await bot.delete_message(user_id, msg_id)
    except Exception:
        pass


async def _send_profile_view(
    user_id: int,
    bot,
    sender: Callable[[], Awaitable[Message]],
) -> Message:
    await _cleanup_profile_message(user_id, bot)
    msg = await sender()
    _profile_message_ids[user_id] = msg.message_id
    return msg


def _review_nav_markup(page: int, has_more: bool) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"profile:reviews:{page-1}"
            )
        )
    nav.append(InlineKeyboardButton(text=f"{page}", callback_data="noop"))
    if has_more:
        nav.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"profile:reviews:{page+1}"
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[nav])


async def _build_review_page(
    sessionmaker: async_sessionmaker, page: int
) -> tuple[str | None, InlineKeyboardMarkup | None]:
    per_page = REVIEWS_PER_PAGE
    limit = per_page + 1
    offset = (page - 1) * per_page
    async with sessionmaker() as session:
        guarantor = aliased(User)
        result = await session.execute(
            select(Review, Deal, User)
            .join(Deal, Deal.id == Review.deal_id)
            .join(User, User.id == Review.author_id)
            .join(guarantor, guarantor.id == Deal.guarantee_id)
            .where(
                Review.status == "active",
                Deal.guarantee_id.is_not(None),
                guarantor.role == "guarantor",
            )
            .order_by(Deal.id.desc(), Review.id.asc())
            .limit(limit)
            .offset(offset)
        )
        rows = result.all()
    has_more = len(rows) > per_page
    rows = rows[:per_page]
    if not rows:
        return None, None

    entries: dict[int, dict[str, object]] = {}
    for review, deal, author in rows:
        entry = entries.setdefault(
            deal.id,
            {
                "deal": deal,
                "seller": {},
                "buyer": {},
            },
        )
        if author.id == deal.seller_id:
            entry["seller"]["comment"] = review.comment
            entry["seller"]["rating"] = review.rating
        elif author.id == deal.buyer_id:
            entry["buyer"]["comment"] = review.comment
            entry["buyer"]["rating"] = review.rating
        entry["guarantor_id"] = deal.guarantee_id
    async with sessionmaker() as session:
        guarantor_ids = {
            entry["guarantor_id"] for entry in entries.values() if entry["guarantor_id"]
        }
        guarantors = {}
        if guarantor_ids:
            result = await session.execute(
                select(User).where(User.id.in_(guarantor_ids))
            )
            guarantors = {user.id: user for user in result.scalars().all()}

    texts: list[str] = []
    sorted_items = sorted(entries.items(), key=lambda item: item[0], reverse=True)
    for deal_id, entry in sorted_items:
        deal: Deal = entry["deal"]
        guarantor = guarantors.get(entry.get("guarantor_id"))
        guarantor_label = (
            f"@{guarantor.username}" if guarantor and guarantor.username else str(guarantor.id)
            if guarantor
            else "-"
        )
        lines = [
            f"Гарант {guarantor_label}",
            f"Сделка №{deal.id}",
        ]
        seller = entry["seller"]
        buyer = entry["buyer"]
        if seller.get("comment"):
            lines.append(f"Отзыв продавца: {seller['comment']}")
        elif seller.get("rating"):
            lines.append(f"Оценка продавца: {seller['rating']}/5")
        if buyer.get("comment"):
            lines.append(f"Отзыв покупателя: {buyer['comment']}")
        elif buyer.get("rating"):
            lines.append(f"Оценка покупателя: {buyer['rating']}/5")
        ratings = [
            seller.get("rating"),
            buyer.get("rating"),
        ]
        ratings = [r for r in ratings if isinstance(r, int)]
        if ratings:
            avg = sum(ratings) / len(ratings)
            lines.append(f"Оценка: {avg:.1f}/5")
        texts.append("\n".join(lines))

    markup = _review_nav_markup(page, has_more)
    return "\n\n".join(texts), markup


class AdEditStates(StatesGroup):
    """Represent AdEditStates.

    Attributes:
        field: Attribute value.
        value: Attribute value.
        ad_id: Attribute value.
        media_type: Attribute value.
        media: Attribute value.
    """

    field = State()
    value = State()
    ad_id = State()
    media_type = State()
    media = State()


class ReviewStates(StatesGroup):
    """Represent ReviewStates.

    Attributes:
        target: Attribute value.
        rating: Attribute value.
        comment: Attribute value.
    """

    target = State()
    rating = State()
    comment = State()


class VipStates(StatesGroup):
    """Represent VipStates.

    Attributes:
        broadcast_text: Attribute value.
    """

    broadcast_text = State()


def _fmt_date(value: datetime | None) -> str:
    """Handle fmt date.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    if not value:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def _role_label(role: str) -> str:
    """Handle role label.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    mapping = {
        "owner": "Владелец",
        "admin": "Главный админ",
        "moderator": "Модератор",
        "designer": "Дизайнер",
        "guarantor": "Гарант",
        "user": "Пользователь",
    }
    return mapping.get(role, role)


def _rating_label(value: int) -> str:
    """Handle rating label.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    return "⭐" * value


def _esc(value: str | None) -> str:
    """Handle esc.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    return html.escape(value or "")


def _status_label(value: str | None) -> str:
    """Handle status label.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    mapping = {
        "requested": "ожидает",
        "in_progress": "в работе",
        "completed": "завершена",
        "cancelled": "отменена",
        "closed": "завершена",
        "canceled": "отменена",
    }
    return mapping.get(value or "", value or "-")


def _deal_type_label(value: str | None) -> str:
    """Handle deal type label.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    mapping = {
        "buy": "покупка",
        "contact": "контакт",
        "exchange": "обмен",
        "exchange_with_addon": "обмен с доплатой",
        "installment": "рассрочка",
    }
    return mapping.get(value or "", value or "-")


def _deals_archive_kb(status: str, period: str) -> InlineKeyboardMarkup:
    """Build archive filters keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Все", callback_data=f"deals_archive:all:{period}"
                ),
                InlineKeyboardButton(
                    text="Закрытые", callback_data=f"deals_archive:closed:{period}"
                ),
                InlineKeyboardButton(
                    text="Отмененные",
                    callback_data=f"deals_archive:canceled:{period}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="В работе",
                    callback_data=f"deals_archive:in_progress:{period}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="7д", callback_data=f"deals_archive:{status}:7d"
                ),
                InlineKeyboardButton(
                    text="30д", callback_data=f"deals_archive:{status}:30d"
                ),
                InlineKeyboardButton(
                    text="90д", callback_data=f"deals_archive:{status}:90d"
                ),
                InlineKeyboardButton(
                    text="Все время", callback_data=f"deals_archive:{status}:all"
                ),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile:back")],
        ]
    )


async def _send_deals_archive(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    *,
    status: str,
    period: str,
) -> None:
    """Send archived deals list with filters."""
    since = None
    if period != "all":
        days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
        since = datetime.utcnow() - timedelta(days=days)

    async with sessionmaker() as session:
        query = select(Deal).where(
            or_(
                Deal.buyer_id == callback.from_user.id,
                Deal.seller_id == callback.from_user.id,
                Deal.guarantee_id == callback.from_user.id,
            )
        )
        if status != "all":
            query = query.where(Deal.status == status)
        if since:
            query = query.where(Deal.created_at >= since)
        result = await session.execute(query.order_by(Deal.id.desc()).limit(20))
        deals = result.scalars().all()

    header = f"🗄 Архив сделок — статус: {status}, период: {period}"
    if not deals:
        await callback.message.answer(
            header + "\n\nСделок не найдено.",
            reply_markup=_deals_archive_kb(status, period),
        )
        await callback.answer()
        return

    buttons = []
    for deal in deals:
        label = f"#{deal.id} {_status_label(deal.status)}"
        buttons.append((deal.id, label))
    await callback.message.answer(
        header,
        reply_markup=_deals_archive_kb(status, period),
    )
    await callback.message.answer(
        "Выберите сделку из архива:", reply_markup=deal_list_kb(buttons)
    )
    await callback.answer()


async def _recalc_rating(session, user_id: int) -> None:
    """Handle recalc rating.

    Args:
        session: Value for session.
        user_id: Value for user_id.
    """
    result = await session.execute(
        select(func.count(Review.id), func.avg(Review.rating)).where(
            Review.target_id == user_id, Review.status == "active"
        )
    )
    count, avg = result.one()
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.rating_count = count or 0
        user.rating_avg = float(avg) if avg is not None else None
        await session.commit()


def _is_profile_button_text(message: Message) -> bool:
    """Check whether a message likely refers to the profile menu button."""
    if not message.text:
        return False
    text = message.text.strip()
    profile_label = "\U0001f464 \u041f\u0440\u043e\u0444\u0438\u043b\u044c"
    if text == profile_label:
        return True
    if "\u041f\u0440\u043e\u0444\u0438\u043b\u044c" in text or "Profile" in text:
        return True
    if text.startswith("\U0001f464"):
        return True
    normalized = re.sub(r"[^\w\u0400-\u04FF]+", "", text).lower()
    return "\u043f\u0440\u043e\u0444\u0438\u043b\u044c" in normalized


@router.message(_is_profile_button_text)
async def profile_main(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle profile main.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    trust_score = 0
    trust_factors: list[str] = []
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if user.id in settings.owner_ids and user.role != "owner":
            user.role = "owner"
            await session.commit()

        result = await session.execute(
            select(
                func.count(Deal.id),
                func.sum(Deal.price),
            ).where(or_(Deal.buyer_id == user.id, Deal.seller_id == user.id))
        )
        deals_total, turnover = result.one()

        result = await session.execute(
            select(Deal.status, func.count(Deal.id))
            .where(or_(Deal.buyer_id == user.id, Deal.seller_id == user.id))
            .group_by(Deal.status)
        )
        status_rows = result.all()

        result = await session.execute(
            select(Ad).where(Ad.seller_id == user.id, Ad.active.is_(True))
        )
        ads = result.scalars().all()

        await apply_deal_no_dispute_bonus(session, user.id)
        trust_score = await get_trust_score(session, user.id)
        trust_factors = await get_trust_factors(session, user.id, limit=2)

    status_lines = ["📌 Статусы сделок:"]
    for status, count in status_rows:
        status_lines.append(f"• {_status_label(status)}: {count}")

    rating = float(user.rating_avg or 0)
    rating_count = user.rating_count or 0
    balance = user.balance or 0
    turnover_value = turnover or 0

    trust_label = (
        "🟢 Надежный"
        if trust_score >= 70
        else "🟡 Средний" if trust_score >= 40 else "🔴 Рискованный"
    )
    trust_factors_text = ""
    if trust_factors:
        trust_factors_text = "Причины снижения: " + ", ".join(trust_factors)
    trust_lines = (
        f"🧭 Trust Score: <b>{trust_score}/100</b> — {trust_label}\n"
        + (f"{trust_factors_text}\n" if trust_factors_text else "")
        + "\n"
    )

    if settings.bot_username:
        referral_link = f"https://t.me/{settings.bot_username}?start={user.id}"
    else:
        referral_link = "не задано"

    text = (
        "<b>👤 Профиль GSNS</b>\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👑 Роль: <b>{_role_label(user.role)}</b>\n"
        f"📅 Регистрация: {_fmt_date(user.created_at)}\n\n"
        f"💰 Баланс: <b>{balance} GSNS Coins</b>\n"
        f"📊 Оборот: <b>{turnover_value} ₽</b>\n"
        f"🤝 Сделок: <b>{deals_total or 0}</b>\n"
        f"⭐ Рейтинг: <b>{rating:.2f}</b> ({rating_count} отзывов)\n\n"
        f"{trust_lines}"
        f"{chr(10).join(status_lines)}\n\n"
        f"📢 Активных объявлений: <b>{len(ads)}</b>\n"
        f"🔗 Реферальная ссылка: {referral_link}"
    )
    await _send_profile_view(
        message.from_user.id,
        message.bot,
        lambda: message.answer(text, reply_markup=profile_actions_kb()),
    )


@router.callback_query(F.data == "profile:wallet")
async def profile_wallet(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle profile wallet.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()
        if not user:
            await callback.message.answer("Профиль не найден.")
            await callback.answer()
            return

        result = await session.execute(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user.id)
            .order_by(WalletTransaction.id.desc())
            .limit(10)
        )
        rows = result.scalars().all()

    usdt_per_rub = usdt_per_rub_rate(settings)
    coins_per_rub = coins_per_rub_rate(settings)
    min_usdt = rub_to_usdt(settings.min_topup_rub, settings)
    min_coins = rub_to_coins(settings.min_topup_rub, settings)

    lines = [
        "<b>💳 Баланс и операции</b>",
        f"💰 Баланс: <b>{user.balance or 0} GSNS Coins</b>",
        f"💱 Курс: 1 ₽ = {usdt_per_rub} USDT = {coins_per_rub} Coins",
        (
            "✅ Минимум пополнения: "
            f"{settings.min_topup_rub} ₽ = {min_usdt} USDT = {min_coins} Coins"
        ),
        "🧾 Последние операции:",
    ]
    await _send_profile_view(
        callback.from_user.id,
        callback.bot,
        lambda: callback.message.answer("\n".join(lines)),
    )
    if not rows:
        await callback.message.answer("Операций пока нет.")
    else:
        for row in rows:
            when = row.created_at.strftime("%Y-%m-%d %H:%M")
            text = (
                f"• <b>{row.type}</b>: {row.amount} Coins\n"
                f"📝 {_esc(row.description or 'без описания')}\n"
                f"🕒 {when}"
            )
            await callback.message.answer(text, reply_markup=wallet_tx_kb(row.id))
    await callback.answer()


@router.callback_query(F.data.startswith("profile:reviews"))
async def profile_reviews(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
) -> None:
    parts = callback.data.split(":")
    page = 1
    if len(parts) > 2 and parts[2].isdigit():
        page = max(int(parts[2]), 1)
    text, markup = await _build_review_page(sessionmaker, page)
    if not text:
        await _send_profile_view(
            callback.from_user.id,
            callback.bot,
            lambda: callback.message.answer("Пока нет отзывов гарантов."),
        )
        await callback.answer()
        return
    await _send_profile_view(
        callback.from_user.id,
        callback.bot,
        lambda: callback.message.answer(text, reply_markup=markup),
    )
    await callback.answer()


@router.message(F.text == REVIEW_MENU_BUTTON, F.chat.type == "private")
async def profile_reviews_menu(
    message: Message, sessionmaker: async_sessionmaker
) -> None:
    """Handle reviews quick access from the main menu."""
    page = 1
    text, markup = await _build_review_page(sessionmaker, page)
    if not text:
        await _send_profile_view(
            message.from_user.id,
            message.bot,
            lambda: message.answer("Пока нет отзывов гарантов."),
        )
        return
    await _send_profile_view(
        message.from_user.id,
        message.bot,
        lambda: message.answer(text, reply_markup=markup),
    )


@router.callback_query(F.data.startswith("wallet_tx:"))
async def wallet_tx_detail(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle wallet tx detail.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    tx_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(
            select(WalletTransaction).where(WalletTransaction.id == tx_id)
        )
        tx = result.scalar_one_or_none()
        if not tx or tx.user_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return

        detail = (
            f"<b>🧾 Операция #{tx.id}</b>\n"
            f"Тип: <b>{tx.type}</b>\n"
            f"Сумма: <b>{tx.amount} Coins</b>\n"
            f"Дата: {tx.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"Описание: {_esc(tx.description or '-')}"
        )

        if tx.ref_type == "service_purchase" and tx.ref_id:
            result = await session.execute(
                select(ServicePurchase, Service)
                .join(Service, Service.id == ServicePurchase.service_id)
                .where(ServicePurchase.id == tx.ref_id)
            )
            row = result.first()
            if row:
                purchase, service = row
                detail += (
                    "\n\n<b>🛒 Покупка услуги</b>\n"
                    f"{_esc(service.title)}\n"
                    f"Категория: {_esc(service.category)}\n"
                    f"Цена: {service.price} Coins\n"
                    f"Статус: {purchase.status}"
                )

    await callback.message.answer(detail)
    await callback.answer()


@router.callback_query(F.data == "profile:service_purchases")
async def profile_service_purchases(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle profile service purchases.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ServicePurchase, Service)
            .join(Service, Service.id == ServicePurchase.service_id)
            .where(ServicePurchase.buyer_id == callback.from_user.id)
            .order_by(ServicePurchase.id.desc())
            .limit(20)
        )
        rows = result.all()

    if not rows:
        await callback.message.answer("Покупок услуг пока нет.")
        await callback.answer()
        return

    for purchase, service in rows:
        text = (
            f"<b>🛒 Покупка #{purchase.id}</b>\n"
            f"{_esc(service.title)}\n"
            f"Категория: {_esc(service.category)}\n"
            f"Цена: {service.price} Coins\n"
            f"Статус: {purchase.status}"
        )
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "profile:deals")
async def profile_deals(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle profile deals.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Deal)
            .where(
                or_(
                    Deal.buyer_id == callback.from_user.id,
                    Deal.seller_id == callback.from_user.id,
                )
            )
            .order_by(Deal.id.desc())
            .limit(20)
        )
        deals = result.scalars().all()

    if not deals:
        await callback.message.answer("Сделок пока нет.")
        await callback.answer()
        return

    buttons = []
    for deal in deals:
        label = f"#{deal.id} {_status_label(deal.status)}"
        buttons.append((deal.id, label))
    await callback.message.answer("🧾 Ваши сделки:", reply_markup=deal_list_kb(buttons))
    await callback.answer()


@router.callback_query(F.data == "profile:deals_archive")
async def profile_deals_archive(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Show deals archive with filters."""
    await _send_deals_archive(callback, sessionmaker, status="closed", period="30d")


@router.callback_query(F.data.startswith("deals_archive:"))
async def deals_archive_filter(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle archive filter updates."""
    _, status, period = callback.data.split(":", 2)
    await _send_deals_archive(callback, sessionmaker, status=status, period=period)


@router.callback_query(F.data.startswith("profile_deal:"))
async def profile_deal_detail(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle profile deal detail.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        seller = aliased(User)
        buyer = aliased(User)
        guarantor = aliased(User)
        result = await session.execute(
            select(Deal, Ad, Game, seller, buyer, guarantor)
            .join(Ad, Ad.id == Deal.ad_id, isouter=True)
            .join(Game, Game.id == Ad.game_id, isouter=True)
            .join(seller, seller.id == Deal.seller_id)
            .join(buyer, buyer.id == Deal.buyer_id)
            .join(guarantor, guarantor.id == Deal.guarantee_id, isouter=True)
            .where(Deal.id == deal_id)
        )
        row = result.first()

    if not row:
        await callback.answer("Сделка не найдена.")
        return

    deal, ad, game, seller, buyer, guarantor = row
    if callback.from_user.id not in {
        deal.buyer_id,
        deal.seller_id,
        deal.guarantee_id,
    }:
        await callback.answer("Нет доступа.")
        return

    text = _deal_text(deal, ad, game, seller, buyer, guarantor)
    deal_chat_url = deal.room_invite_link if deal.room_ready else None
    await callback.message.answer(
        text,
        reply_markup=deal_detail_kb(deal.id, deal_chat_url=deal_chat_url),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("export_deal:"))
async def export_deal(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle export deal.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        seller = aliased(User)
        buyer = aliased(User)
        guarantor = aliased(User)
        result = await session.execute(
            select(Deal, Ad, Game, seller, buyer, guarantor)
            .join(Ad, Ad.id == Deal.ad_id, isouter=True)
            .join(Game, Game.id == Ad.game_id, isouter=True)
            .join(seller, seller.id == Deal.seller_id)
            .join(buyer, buyer.id == Deal.buyer_id)
            .join(guarantor, guarantor.id == Deal.guarantee_id, isouter=True)
            .where(Deal.id == deal_id)
        )
        row = result.first()

    if not row:
        await callback.answer("Сделка не найдена.")
        return

    deal, ad, game, seller, buyer, guarantor = row
    if callback.from_user.id not in {
        deal.buyer_id,
        deal.seller_id,
        deal.guarantee_id,
    }:
        await callback.answer("Нет доступа.")
        return

    content = _deal_text(deal, ad, game, seller, buyer, guarantor)
    data = content.encode("utf-8")
    file = BufferedInputFile(data, filename=f"deal_{deal.id}.txt")
    await callback.message.answer_document(file)
    await callback.answer()


@router.callback_query(F.data.startswith("review_start:"))
async def review_start(
    callback: CallbackQuery, sessionmaker: async_sessionmaker, state: FSMContext
) -> None:
    """Handle review start.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        state: Value for state.
    """
    deal_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await callback.answer("Сделка не найдена.")
            return
        if deal.status != "closed":
            await callback.answer("Отзыв доступен после завершения сделки.")
            return

    if callback.from_user.id not in {
        deal.buyer_id,
        deal.seller_id,
        deal.guarantee_id,
    }:
        await callback.answer("Нет доступа.")
        return

    targets = []
    if deal.guarantee_id:
        targets.append(("Гарант", deal.guarantee_id))
    if callback.from_user.id == deal.buyer_id:
        targets.append(("Продавец", deal.seller_id))
    elif callback.from_user.id == deal.seller_id:
        targets.append(("Покупатель", deal.buyer_id))

    if not targets:
        await callback.answer("Нет доступных участников для отзыва.")
        return

    buttons = []
    for label, target_id in targets:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"review_target:{deal_id}:{target_id}"
                )
            ]
        )
    await state.update_data(deal_id=deal_id)
    await callback.message.answer(
        "⭐ Кого хотите оценить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("review_target:"))
async def review_target(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle review target.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    _, deal_id, target_id = callback.data.split(":")
    await state.update_data(deal_id=int(deal_id), target_id=int(target_id))
    await state.set_state(ReviewStates.rating)
    await callback.message.answer("⭐ Оцените от 1 до 5.")
    await callback.answer()


@router.message(ReviewStates.rating)
async def review_rating(message: Message, state: FSMContext) -> None:
    """Handle review rating.

    Args:
        message: Value for message.
        state: Value for state.
    """
    try:
        rating = int(message.text.strip())
    except ValueError:
        await message.answer("⭐ Оцените от 1 до 5.")
        return
    if rating < 1 or rating > 5:
        await message.answer("⭐ Оцените от 1 до 5.")
        return
    await state.update_data(rating=rating)
    await state.set_state(ReviewStates.comment)
    await message.answer("💬 Комментарий (можно написать «пропустить»):")


@router.message(ReviewStates.comment)
async def review_comment(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle review comment.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    deal_id = data.get("deal_id")
    target_id = data.get("target_id")
    rating = data.get("rating")
    if not deal_id or not target_id or not rating:
        await state.clear()
        await message.answer("⏱️ Сеанс оценки истек.")
        return

    comment = message.text.strip()
    if comment.lower() == "пропустить":
        comment = ""

    async with sessionmaker() as session:
        result = await session.execute(select(Deal).where(Deal.id == deal_id))
        deal = result.scalar_one_or_none()
        if not deal:
            await state.clear()
            await message.answer("Сделка не найдена.")
            return
        if deal.status != "closed":
            await state.clear()
            await message.answer("Отзыв доступен после завершения сделки.")
            return
        if message.from_user.id not in {
            deal.buyer_id,
            deal.seller_id,
            deal.guarantee_id,
        }:
            await state.clear()
            await message.answer("Нет доступа.")
            return
        valid_targets = {
            deal.buyer_id,
            deal.seller_id,
            deal.guarantee_id,
        }
        if target_id not in valid_targets or target_id == message.from_user.id:
            await state.clear()
            await message.answer("Неверный получатель отзыва.")
            return

        result = await session.execute(
            select(Review).where(
                Review.deal_id == deal_id,
                Review.author_id == message.from_user.id,
                Review.target_id == target_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.rating = rating
            existing.comment = comment
            existing.status = "active"
            await session.commit()
            await _recalc_rating(session, target_id)
            await state.clear()
            await message.answer("Отзыв обновлен.")
            return

        review = Review(
            deal_id=deal_id,
            author_id=message.from_user.id,
            target_id=target_id,
            rating=rating,
            comment=comment,
        )
        session.add(review)
        await session.commit()
        if rating >= 4:
            await apply_trust_event(
                session,
                target_id,
                "positive_review",
                4,
                "Положительный отзыв",
                ref_type="review",
                ref_id=review.id,
            )
        await _recalc_rating(session, target_id)

    await state.clear()
    await message.answer(f"✅ Отзыв сохранен: {_rating_label(rating)}")


@router.callback_query(F.data == "profile:ads")
async def profile_ads(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle profile ads.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Ad, Game)
            .join(Game, Game.id == Ad.game_id)
            .where(Ad.seller_id == callback.from_user.id)
            .order_by(Ad.id.desc())
        )
        rows = result.all()

    if not rows:
        await callback.message.answer("У вас пока нет объявлений.")
        await callback.answer()
        return

    for ad, game in rows:
        caption = (
            f"🧾 {_esc(ad.title)}\n"
            f"🎮 Игра: {_esc(game.name)}\n"
            f"💰 Цена: {ad.price} ₽\n"
            f"📌 Статус: {'активно' if ad.active else 'скрыто'}"
        )
        if ad.media_type == "фото" and ad.media_file_id:
            await callback.message.answer_photo(
                ad.media_file_id,
                caption=caption,
                reply_markup=my_ad_manage_kb(ad.id),
            )
        elif ad.media_type == "видео" and ad.media_file_id:
            await callback.message.answer_video(
                ad.media_file_id,
                caption=caption,
                reply_markup=my_ad_manage_kb(ad.id),
            )
        else:
            await callback.message.answer(caption, reply_markup=my_ad_manage_kb(ad.id))

    await callback.answer()


@router.callback_query(F.data == "profile:vip")
async def profile_vip(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle profile vip.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

    if not user:
        await callback.message.answer("Профиль не найден.")
        await callback.answer()
        return

    vip_label = (
        f"активен до {_fmt_date(user.vip_until)}"
        if is_vip_until(user.vip_until)
        else "не активен"
    )
    free_deal_label = (
        f"активна до {_fmt_date(user.free_fee_until)}"
        if free_fee_active(user.free_fee_until)
        else "нет"
    )
    text = (
        "<b>💎 VIP статус GSNS</b>\n"
        f"Статус: <b>{vip_label}</b>\n"
        f"🎁 Бесплатная сделка: <b>{free_deal_label}</b>\n\n"
        "<b>Преимущества VIP:</b>\n"
        "• Автопродвижение объявлений 2 раза в день\n"
        "• VIP-очередь у гарантов (приоритет в обработке сделок)\n"
        "• VIP-метка и знак «Проверенный продавец» в витрине\n"
        "• Скидка на комиссии через GSNS Trade (сделки от 2500 ₽):\n"
        "  – К/П: −1 п.п. от базовой ставки\n"
        "  – Обмен: 370 ₽\n"
        "  – Обмен с доплатой: 370 ₽ + 9% от доплаты\n"
        "  – Рассрочка: 12%\n"
        "• 1 купон в месяц: −50% на комиссию одной сделки\n"
        "• Витрина VIP / «VIP-лот дня» после модерации по очереди среди VIP\n\n"
        "<b>Платные опции:</b>\n"
        "• Бесплатная сделка на неделю — 6000 Coins"
    )
    await callback.message.answer(text, reply_markup=vip_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "vip:broadcast")
async def vip_broadcast_start(
    callback: CallbackQuery, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle vip broadcast start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    await state.clear()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()
        if not user:
            await callback.answer("Профиль не найден.")
            return
        if not is_vip_until(user.vip_until):
            await callback.message.answer("VIP не активен. Активируйте VIP.")
            await callback.answer()
            return

    await state.set_state(VipStates.broadcast_text)
    await callback.message.answer(
        "📣 Введите текст рассылки.\n" "Стоимость: 3000 Coins. Лимит: 3 раза в день."
    )
    await callback.answer()


@router.message(VipStates.broadcast_text)
async def vip_broadcast_text(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle vip broadcast text.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите текст рассылки.")
        return

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not is_vip_until(user.vip_until):
            await state.clear()
            await message.answer("VIP не активен. Активируйте VIP.")
            return

        now = datetime.utcnow()
        if user.paid_broadcasts_date is None or (
            user.paid_broadcasts_date.date() != now.date()
        ):
            user.paid_broadcasts_date = now
            user.paid_broadcasts_count = 0
        if (user.paid_broadcasts_count or 0) >= 3:
            await state.clear()
            await message.answer("Лимит рассылок на сегодня исчерпан.")
            return
        if (user.balance or 0) < 3000:
            await state.clear()
            await message.answer("Недостаточно GSNS Coins.")
            return

        user.balance = (user.balance or 0) - 3000
        user.paid_broadcasts_count = (user.paid_broadcasts_count or 0) + 1
        session.add(
            WalletTransaction(
                user_id=user.id,
                amount=-3000,
                type="broadcast",
                description="Платная рассылка",
            )
        )
        await session.flush()
        await create_broadcast_request(
            session,
            message.bot,
            settings,
            creator_id=user.id,
            text=text,
            kind="paid",
            cost=3000,
        )

    await state.clear()
    await message.answer("✅ Заявка на рассылку отправлена модератору.")


@router.callback_query(F.data == "vip:free_deal")
async def vip_free_deal(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle vip free deal.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not is_vip_until(user.vip_until):
            await callback.message.answer("VIP не активен. Активируйте VIP.")
            await callback.answer()
            return
        if (user.balance or 0) < 6000:
            await callback.message.answer("Недостаточно GSNS Coins.")
            await callback.answer()
            return

        now = datetime.utcnow()
        base = (
            user.free_fee_until
            if user.free_fee_until and user.free_fee_until > now
            else now
        )
        user.free_fee_until = base + timedelta(days=7)
        user.balance = (user.balance or 0) - 6000
        session.add(
            WalletTransaction(
                user_id=user.id,
                amount=-6000,
                type="free_deal_week",
                description="Бесплатная сделка на 7 дней",
            )
        )
        await session.commit()

    await callback.message.answer(
        f"✅ Бесплатная сделка активна до {_fmt_date(user.free_fee_until)}."
    )
    await callback.answer()


@router.callback_query(F.data == "profile:back")
async def profile_back(callback: CallbackQuery) -> None:
    """Handle profile back.

    Args:
        callback: Value for callback.
    """
    await callback.message.answer("↩️ Откройте «👤 Профиль» в главном меню.")
    await callback.answer()


@router.callback_query(F.data.startswith("delete_ad:"))
async def delete_ad(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    """Delete ad.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    ad_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        await session.delete(ad)
        await session.commit()

    await callback.message.answer("🗑️ Объявление удалено.")
    await callback.answer()


@router.callback_query(F.data.startswith("edit_ad:"))
async def edit_ad(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle edit ad.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    ad_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(ad_id=ad_id)
    await callback.message.answer(
        "Что хотите изменить?", reply_markup=ad_edit_kb(ad_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_field:"))
async def edit_field(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle edit field.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    _, field, ad_id_raw = callback.data.split(":")
    await state.update_data(field=field, ad_id=int(ad_id_raw))
    if field == "media":
        await state.set_state(AdEditStates.media_type)
        await callback.message.answer("🖼️ Выберите тип медиа: Фото или Видео.")
        await callback.answer()
        return
    if field == "game":
        await state.set_state(AdEditStates.value)
        await callback.answer()
        await _prompt_game_edit(callback, sessionmaker)
        return

    await state.set_state(AdEditStates.value)
    await callback.message.answer("✏️ Введите новое значение:")
    await callback.answer()


async def _prompt_game_edit(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle prompt game edit.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        games = await session.execute(
            select(Game.id, Game.name).where(Game.active.is_(True))
        )
        game_list = games.all()

    if not game_list:
        await callback.message.answer("Нет доступных игр.")
        return

    await callback.message.answer(
        "🎮 Выберите новую игру:",
        reply_markup=game_list_kb(game_list, "edit_game"),
    )


@router.callback_query(F.data.startswith("edit_game:"))
async def edit_game(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle edit game.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    game_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    ad_id = data.get("ad_id")
    if not ad_id:
        await callback.answer("⏱️ Сеанс редактирования истек.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        ad.game_id = game_id
        await session.commit()

    await state.clear()
    await callback.message.answer("✅ Игра изменена.")
    await callback.answer()


@router.message(AdEditStates.media_type)
async def edit_media_type(message: Message, state: FSMContext) -> None:
    """Handle edit media type.

    Args:
        message: Value for message.
        state: Value for state.
    """
    choice = message.text.strip().lower()
    if choice not in {"фото", "видео"}:
        await message.answer("🖼️ Выберите Фото или Видео.")
        return
    await state.update_data(media_type=choice)
    await state.set_state(AdEditStates.media)
    await message.answer(f"📎 Отправьте {choice}.")


@router.message(AdEditStates.media)
async def edit_media(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle edit media.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    ad_id = data.get("ad_id")
    media_type = data.get("media_type")
    if not ad_id or not media_type:
        await state.clear()
        await message.answer("⏱️ Сеанс редактирования истек.")
        return

    if media_type == "фото":
        if not message.photo:
            await message.answer("📸 Отправьте фото.")
            return
        file_id = message.photo[-1].file_id
    else:
        if not message.video:
            await message.answer("🎥 Отправьте видео.")
            return
        file_id = message.video.file_id

    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != message.from_user.id:
            await message.answer("Нет доступа.")
            await state.clear()
            return
        ad.media_type = media_type
        ad.media_file_id = file_id
        await session.commit()

    await state.clear()
    await message.answer("✅ Медиа обновлено.")


@router.callback_query(F.data.startswith("toggle_ad:"))
async def toggle_ad(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    """Handle toggle ad.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    ad_id = int(callback.data.split(":")[1])
    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        ad.active = not ad.active
        await session.commit()

    if ad.active:
        await callback.message.answer("✅ Объявление опубликовано.")
    else:
        await callback.message.answer("🙈 Объявление скрыто.")
    await callback.answer()


@router.message(AdEditStates.value)
async def edit_field_value(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle edit field value.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    ad_id = data.get("ad_id")
    field = data.get("field")
    if not ad_id or not field:
        await state.clear()
        await message.answer("⏱️ Сеанс редактирования истек.")
        return

    new_value = message.text.strip()
    if field == "price":
        try:
            new_value = Decimal(new_value.replace(",", "."))
        except (InvalidOperation, AttributeError):
            await message.answer("Некорректная цена. Пример: 1500.")
            return

    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != message.from_user.id:
            await message.answer("Нет доступа.")
            await state.clear()
            return

        if field == "title":
            ad.title = new_value
        elif field == "description":
            ad.description = new_value
        elif field == "price":
            ad.price = new_value
        elif field == "payment":
            ad.payment_methods = new_value
        else:
            await message.answer("Это поле нельзя изменить.")
            await state.clear()
            return

        await session.commit()

    await state.clear()
    await message.answer("✅ Объявление обновлено.")


def _deal_text(
    deal: Deal,
    ad: Ad | None,
    game: Game | None,
    seller: User,
    buyer: User,
    guarantor: User | None,
) -> str:
    """Handle deal text.

    Args:
        deal: Value for deal.
        ad: Value for ad.
        game: Value for game.
        seller: Value for seller.
        buyer: Value for buyer.
        guarantor: Value for guarantor.

    Returns:
        Return value.
    """
    game_name = game.name if game else "-"
    ad_title = ad.title if ad else "-"
    description = ad.description if ad else "-"
    payment = ad.payment_methods if ad and ad.payment_methods else "-"
    seller_label = (
        f"{seller.id} (@{seller.username})" if seller.username else str(seller.id)
    )
    buyer_label = f"{buyer.id} (@{buyer.username})" if buyer.username else str(buyer.id)
    guarantor_label = (
        f"{guarantor.id} (@{guarantor.username})"
        if guarantor and guarantor.username
        else (str(guarantor.id) if guarantor else "-")
    )
    return (
        f"<b>📄 Сделка #{deal.id}</b>\n"
        f"Статус: <b>{_status_label(deal.status)}</b>\n"
        f"Тип: {_deal_type_label(deal.deal_type)}\n"
        f"Игра: {_esc(game_name)}\n"
        f"Лот: {_esc(ad_title)}\n"
        f"Описание: {_esc(description)}\n"
        f"Цена: {deal.price or '-'} ₽\n"
        f"Комиссия: {deal.fee or 0} ₽\n"
        f"Оплата: {_esc(payment)}\n"
        f"Продавец: {seller_label}\n"
        f"Покупатель: {buyer_label}\n"
        f"Гарант: {guarantor_label}\n"
        f"Создана: {deal.created_at.strftime('%Y-%m-%d %H:%M')}"
    )
