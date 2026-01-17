"""Module for ads functionality."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import Ad, Complaint, Game, User
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.ads import (
    ad_actions_kb,
    account_filter_kb,
    exchange_actions_kb,
    game_list_kb,
    my_ad_kb,
)
from bot.keyboards.common import deals_menu_kb, exchange_menu_kb
from bot.keyboards.staff import confirm_action_kb
from bot.services.trust import apply_trust_event
from bot.utils.admin_target import get_admin_target
from bot.utils.scammers import find_scammer
from bot.utils.vip import is_vip_until

router = Router()

_CUSTOM_EMOJI_RE = re.compile(r"<tg-emoji[^>]*>")


def _count_custom_emoji_html(html: str | None) -> int:
    """Count custom emoji markers in HTML."""
    if not html:
        return 0
    return len(_CUSTOM_EMOJI_RE.findall(html))


def _count_custom_emoji_entities(message: Message | None) -> int:
    """Count custom emoji entities in a Telegram message."""
    if not message:
        return 0
    entities = (message.entities or []) + (message.caption_entities or [])
    return sum(1 for entity in entities if entity.type == "custom_emoji")


async def _notify_custom_emoji_loss(
    bot,
    sessionmaker: async_sessionmaker,
    *,
    context: str,
    expected: int,
    actual: int,
    chat_id: int | None = None,
    ad_id: int | None = None,
) -> None:
    """Notify moderators when custom emojis are lost on send."""
    text = (
        "⚠️ Telegram удалил часть кастомных эмоджи.\n"
        f"Контекст: {context}\n"
        f"Ожидалось: {expected}\n"
        f"Фактически: {actual}\n"
        f"Чат: {chat_id or '-'}\n"
        f"Объявление: {ad_id or '-'}"
    )
    await _notify_moderators(bot, sessionmaker, text)


class AdCreateStates(StatesGroup):
    """Represent AdCreateStates.

    Attributes:
        game_id: Attribute value.
        title: Attribute value.
        description: Attribute value.
        is_account: Attribute value.
        account_id: Attribute value.
        media_type: Attribute value.
        media: Attribute value.
        price: Attribute value.
        payment: Attribute value.
    """

    game_id = State()
    title = State()
    description = State()
    is_account = State()
    account_id = State()
    media_type = State()
    media = State()
    price = State()
    payment = State()


class ExchangeOfferStates(StatesGroup):
    """Represent ExchangeOfferStates.

    Attributes:
        game_name: Attribute value.
        want: Attribute value.
        account_id: Attribute value.
        media_type: Attribute value.
        media: Attribute value.
        addon_choice: Attribute value.
        addon_amount: Attribute value.
    """

    game_name = State()
    want = State()
    account_id = State()
    media_type = State()
    media = State()
    addon_choice = State()
    addon_amount = State()


class ComplaintStates(StatesGroup):
    """Represent ComplaintStates.

    Attributes:
        ad_id: Attribute value.
        reason: Attribute value.
    """

    ad_id = State()
    reason = State()


async def _notify_moderators(
    bot,
    sessionmaker: async_sessionmaker,
    text: str,
    media_type: str | None = None,
    media_file_id: str | None = None,
    parse_mode: str | None = None,
) -> None:
    """Handle notify moderators.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        text: Value for text.
        media_type: Value for media_type.
        media_file_id: Value for media_file_id.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.role.in_({"owner", "admin", "moderator"}))
        )
        moderators = result.scalars().all()
    if not moderators:
        return

    expected_emojis = _count_custom_emoji_html(text) if parse_mode == "HTML" else 0
    reported = False

    for mod in moderators:
        response = None
        if media_type == "фото" and media_file_id:
            response = await bot.send_photo(
                mod.id, media_file_id, caption=text, parse_mode=parse_mode
            )
        elif media_type == "видео" and media_file_id:
            response = await bot.send_video(
                mod.id, media_file_id, caption=text, parse_mode=parse_mode
            )
        else:
            response = await bot.send_message(mod.id, text, parse_mode=parse_mode)

        if expected_emojis and not reported:
            actual_emojis = _count_custom_emoji_entities(response)
            if actual_emojis < expected_emojis:
                reported = True
                await _notify_custom_emoji_loss(
                    bot,
                    sessionmaker,
                    context="moderation_notify",
                    expected=expected_emojis,
                    actual=actual_emojis,
                    chat_id=mod.id,
                )


def _format_complaint_notification(
    complaint: Complaint,
    ad: Ad | None,
    game: Game | None,
    reporter: User | None,
) -> str:
    game_line = f"🎮 Игра: {game.name}\n" if game else ""
    if ad:
        account_line = f"🆔 ID аккаунта: {ad.account_id}\n" if ad.account_id else ""
        price_line = (
            f"💰 Цена: {ad.price:.2f} ₽\n" if ad.price is not None else "💰 Цена: Договорная\n"
        )
        description_line = f"📜 Описание: {ad.description or '-'}\n"
        ad_details = (
            f"🔖 Название: {ad.title}\n"
            f"{price_line}"
            f"{account_line}"
            f"{description_line}"
        )
    else:
        ad_details = "🔖 Объявление не найдено\n"
    reporter_label = (
        f"{reporter.id} (@{reporter.username})"
        if reporter
        and reporter.username
        else str(reporter.id) if reporter
        else "Автор не найден"
    )
    return (
        f"Жалоба #{complaint.id}\n"
        f"Автор жалобы: {reporter_label}\n"
        f"{game_line}"
        f"Объявление: {complaint.ad_id}\n"
        f"{ad_details}"
        f"Причина: {complaint.reason or '-'}"
    )


def _is_cancel(text: str | None) -> bool:
    """Handle is cancel.

    Args:
        text: Value for text.

    Returns:
        Return value.
    """
    if not text:
        return False
    normalized = text.strip().lower()
    return normalized in {"отмена", "назад", "/cancel", "⬅️ назад"}


def _account_kb() -> ReplyKeyboardMarkup:
    """Handle account kb.

    Returns:
        Return value.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _media_type_kb() -> ReplyKeyboardMarkup:
    """Handle media type kb.

    Returns:
        Return value.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Фото"), KeyboardButton(text="Видео")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _payment_kb() -> ReplyKeyboardMarkup:
    """Handle payment kb.

    Returns:
        Return value.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Карта"), KeyboardButton(text="Крипта")],
            [KeyboardButton(text="Крипта+Карта")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _yes_no_kb() -> ReplyKeyboardMarkup:
    """Handle yes no kb.

    Returns:
        Return value.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _ads_page_callback(
    ad_kind: str,
    page: int,
    game_id: int | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
) -> str:
    """Build callback payload for ads pagination."""
    encoded_min = str(price_min) if price_min is not None else "n"
    encoded_max = str(price_max) if price_max is not None else "n"
    encoded_game = str(game_id or 0)
    return f"ads_page:{ad_kind}:{page}:{encoded_game}:{encoded_min}:{encoded_max}"


def _ads_nav_row(
    ad_kind: str,
    *,
    page: int,
    total_pages: int,
    game_id: int | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
) -> list[InlineKeyboardButton] | None:
    """Build the navigation row for ads pagination."""
    if total_pages <= 1:
        return None
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="<",
                callback_data=_ads_page_callback(
                    ad_kind, page - 1, game_id, price_min, price_max
                ),
            )
        )
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text=">",
                callback_data=_ads_page_callback(
                    ad_kind, page + 1, game_id, price_min, price_max
                ),
            )
        )
    return nav


def _build_ads_list_kb(
    ad_id: int,
    *,
    ad_kind: str,
    page: int,
    total_pages: int,
    game_id: int | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    action_kb = (
        exchange_actions_kb(ad_id)
        if ad_kind == "exchange"
        else ad_actions_kb(ad_id)
    )
    rows.extend(action_kb.inline_keyboard)
    nav_row = _ads_nav_row(
        ad_kind,
        page=page,
        total_pages=total_pages,
        game_id=game_id,
        price_min=price_min,
        price_max=price_max,
    )
    if nav_row:
        rows.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_ad_caption(
    ad: Ad,
    game: Game | None,
    seller: User | None,
    *,
    ad_kind: str,
    page: int | None = None,
    total_pages: int | None = None,
) -> str:
    title_html = ad.title_html or escape(ad.title)
    description_html = ad.description_html or escape(ad.description)
    game_name = escape(game.name) if game else "-"
    if ad_kind == "exchange":
        price_line = f"💰 Доплата: {ad.price or 0} ₽\n"
    else:
        if ad.price is None:
            price_line = "💰 Цена: Договорная\n"
        else:
            price_line = f"💰 Цена: {ad.price} ₽\n"
    vip_badge = is_vip_until(seller.vip_until if seller else None)
    if vip_badge and ad_kind == "sale":
        price_label = (
            f"{ad.price:,.2f}".replace(",", " ") + " ₽"
            if ad.price is not None
            else "Договорная"
        )
        caption = (
            f"🚗 VIP Объявление: {title_html}\n"
            "Новое поступление от проверенного продавца!\n"
            "Этот аккаунт прошел проверку и готов к передаче новому владельцу. "
            "Идеальный выбор для тех, кто ценит статус и качество.\n\n"
            "💰 Стоимость\n\n"
            f"{price_label}\n\n"
            "Заинтересовало предложение? Перейдите в личный кабинет для связи с продавцом или бронирования."
        )
    else:
        caption = (
            f"<b>{title_html}</b>\n"
            f"🎮 Игра: {game_name}\n"
            f"{price_line}"
            f"{description_html}"
        )
    if page is not None and total_pages is not None:
        caption = f"{caption}\n\nСтраница {page}/{total_pages}"
    return caption


async def _edit_ads_message(
    message: Message,
    *,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None,
    has_media: bool,
    media_type: str | None,
    media_file_id: str | None,
) -> Message:
    if has_media and media_file_id:
        if message.photo or message.video:
            media = (
                InputMediaVideo(
                    media=media_file_id,
                    caption=caption,
                    parse_mode="HTML",
                )
                if media_type == "видео"
                else InputMediaPhoto(
                    media=media_file_id,
                    caption=caption,
                    parse_mode="HTML",
                )
            )
            try:
                return await message.edit_media(
                    media=media, reply_markup=reply_markup
                )
            except TelegramBadRequest:
                pass
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        if media_type == "видео":
            return await message.answer_video(
                media_file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        return await message.answer_photo(
            media_file_id,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    if message.photo or message.video:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        return await message.answer(
            caption,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    try:
        return await message.edit_text(
            caption,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        return await message.answer(
            caption,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )


async def _send_ads(
    message: Message,
    sessionmaker: async_sessionmaker,
    *,
    ad_kind: str,
    game_id: int | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    page: int = 1,
    edit_message: bool = False,
) -> None:
    """Handle send ads.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        ad_kind: Value for ad_kind.
    """
    page = max(page, 1)
    per_page = 1
    filters = [
        Ad.active.is_(True),
        Ad.moderation_status == "approved",
        Ad.ad_kind == ad_kind,
    ]
    if ad_kind == "sale":
        if game_id and game_id > 0:
            filters.append(Ad.game_id == game_id)
        if price_min is not None:
            filters.append(Ad.price >= price_min)
        if price_max is not None:
            filters.append(Ad.price <= price_max)

    async with sessionmaker() as session:
        total = await session.scalar(
            select(func.count()).select_from(Ad).where(*filters)
        )
        total = total or 0
        total_pages = max((total + per_page - 1) // per_page, 1)
        page = min(page, total_pages)
        query = (
            select(Ad, Game, User)
            .join(Game, Game.id == Ad.game_id)
            .join(User, User.id == Ad.seller_id)
            .where(*filters)
        )
        if ad_kind == "sale":
            query = query.order_by(
                Ad.promoted_at.is_(None),
                desc(Ad.promoted_at),
                Ad.created_at.desc(),
            )
        else:
            query = query.order_by(Ad.created_at.desc())
        result = await session.execute(
            query.limit(per_page).offset((page - 1) * per_page)
        )
        rows = result.all()

    if not rows:
        empty_text = (
            "Пока нет активных предложений обмена."
            if ad_kind == "exchange"
            else "Пока нет объявлений."
        )
        if edit_message:
            await _edit_ads_message(
                message,
                caption=empty_text,
                reply_markup=None,
                has_media=False,
                media_type=None,
                media_file_id=None,
            )
        else:
            await message.answer(empty_text)
        return

    ad, game, seller = rows[0]
    caption = _format_ad_caption(
        ad,
        game,
        seller,
        ad_kind=ad_kind,
        page=page,
        total_pages=total_pages,
    )
    has_media = bool(ad.media_file_id and ad.media_type in {"фото", "видео"})
    actions_kb = _build_ads_list_kb(
        ad.id,
        ad_kind=ad_kind,
        page=page,
        total_pages=total_pages,
        game_id=game_id,
        price_min=price_min,
        price_max=price_max,
    )
    if edit_message:
        await _edit_ads_message(
            message,
            caption=caption,
            reply_markup=actions_kb,
            has_media=has_media,
            media_type=ad.media_type,
            media_file_id=ad.media_file_id,
        )
        return

    expected_emojis = _count_custom_emoji_html(caption)
    if has_media and ad.media_file_id:
        if ad.media_type == "видео":
            response = await message.answer_video(
                ad.media_file_id,
                caption=caption,
                reply_markup=actions_kb,
                parse_mode="HTML",
            )
        else:
            response = await message.answer_photo(
                ad.media_file_id,
                caption=caption,
                reply_markup=actions_kb,
                parse_mode="HTML",
            )
    else:
        response = await message.answer(
            caption,
            reply_markup=actions_kb,
            parse_mode="HTML",
        )
    if expected_emojis:
        actual_emojis = _count_custom_emoji_entities(response)
        if actual_emojis < expected_emojis:
            await _notify_custom_emoji_loss(
                message.bot,
                sessionmaker,
                context="ads_publish",
                expected=expected_emojis,
                actual=actual_emojis,
                chat_id=message.chat.id,
                ad_id=ad.id,
            )


async def _send_exchanges(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Handle send exchanges.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    await _send_ads(message, sessionmaker, ad_kind="exchange", page=1)


@router.message(F.text == "🛒 Продать аккаунт")
async def create_ad_start(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Create ad start.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    await state.clear()
    await state.set_state(AdCreateStates.game_id)

    async with sessionmaker() as session:
        result = await session.execute(
            select(Game.id, Game.name).where(Game.active.is_(True)).order_by(Game.name)
        )
        games = result.all()

    if not games:
        await message.answer("Список игр пуст. Попросите администратора добавить игру.")
        return

    await message.answer("🎮 Выберите игру:", reply_markup=game_list_kb(games))


@router.message(F.text == "🗂 Все объявления")
async def all_ads(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Handle all ads.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    await _show_games_page(message, sessionmaker, page=1)


async def _show_games_page(
    message: Message,
    sessionmaker: async_sessionmaker,
    *,
    page: int,
    edit_message: bool = False,
) -> None:
    """Show paginated games list for sale filters."""
    page = max(page, 1)
    per_page = 5
    async with sessionmaker() as session:
        total = await session.scalar(
            select(func.count()).select_from(Game).where(Game.active.is_(True))
        )
        total = total or 0
        total_pages = max((total + per_page - 1) // per_page, 1)
        page = min(page, total_pages)
        result = await session.execute(
            select(Game.id, Game.name)
            .where(Game.active.is_(True))
            .order_by(Game.name)
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        games = result.all()
    text = "🎮 Выберите игру для фильтра:"
    reply_markup = game_list_kb(
        games,
        prefix="filter_game",
        page=page,
        total_pages=total_pages,
        include_all=True,
    )
    if edit_message:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            await message.answer(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("game_page:"))
async def game_page(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    """Handle game pagination."""
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, AttributeError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    await _show_games_page(
        callback.message,
        sessionmaker,
        page=page,
        edit_message=True,
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery) -> None:
    """Ignore noop callbacks."""
    await callback.answer()


@router.callback_query(F.data.startswith("filter_game:"))
async def filter_game(callback: CallbackQuery) -> None:
    """Handle game filter selection."""
    raw = callback.data.split(":", 1)[1]
    try:
        game_id = int(raw)
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    text = "💰 Выберите диапазон цен:"
    reply_markup = account_filter_kb(game_id if game_id > 0 else None)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("account_filter:"))
async def account_filter(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle account filter by price."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    try:
        game_id = int(parts[1])
    except ValueError:
        game_id = 0
    range_value = parts[2]
    price_min = None
    price_max = None
    if range_value == "all":
        pass
    elif range_value.endswith("+"):
        try:
            price_min = Decimal(range_value[:-1])
        except InvalidOperation:
            await callback.answer("Некорректные данные.", show_alert=True)
            return
    elif "-" in range_value:
        min_raw, max_raw = range_value.split("-", 1)
        try:
            price_min = Decimal(min_raw)
            price_max = Decimal(max_raw)
        except InvalidOperation:
            await callback.answer("Некорректные данные.", show_alert=True)
            return
    else:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    await _send_ads(
        callback.message,
        sessionmaker,
        ad_kind="sale",
        game_id=game_id or None,
        price_min=price_min,
        price_max=price_max,
        page=1,
        edit_message=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ads_page:"))
async def ads_page(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    """Handle ads pagination."""
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    ad_kind = parts[1]
    if ad_kind not in {"sale", "exchange"}:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    try:
        page = int(parts[2])
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    try:
        game_id = int(parts[3])
    except ValueError:
        game_id = 0
    raw_min = parts[4]
    raw_max = parts[5]
    try:
        price_min = None if raw_min == "n" else Decimal(raw_min)
        price_max = None if raw_max == "n" else Decimal(raw_max)
    except InvalidOperation:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    await _send_ads(
        callback.message,
        sessionmaker,
        ad_kind=ad_kind,
        game_id=game_id or None,
        price_min=price_min if ad_kind == "sale" else None,
        price_max=price_max if ad_kind == "sale" else None,
        page=page,
        edit_message=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("game:"))
async def create_ad_game(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Create ad game.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    try:
        game_id = int(callback.data.split(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    async with sessionmaker() as session:
        result = await session.execute(
            select(Game).where(Game.id == game_id, Game.active.is_(True))
        )
        game = result.scalar_one_or_none()

    if not game:
        await callback.answer("Игра не найдена.", show_alert=True)
        return

    await state.update_data(game_id=game.id)
    await state.set_state(AdCreateStates.title)
    await callback.message.answer(
        "🧾 Введите название объявления:",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()


@router.message(AdCreateStates.title)
async def create_ad_title(message: Message, state: FSMContext) -> None:
    """Create ad title.

    Args:
        message: Value for message.
        state: Value for state.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=deals_menu_kb())
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Введите название объявления.")
        return
    await state.update_data(title=title, title_html=message.html_text or title)
    await state.set_state(AdCreateStates.description)
    await message.answer("📝 Введите описание:")


@router.message(AdCreateStates.description)
async def create_ad_description(message: Message, state: FSMContext) -> None:
    """Create ad description.

    Args:
        message: Value for message.
        state: Value for state.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=deals_menu_kb())
        return
    description = (message.text or "").strip()
    if not description:
        await message.answer("Введите описание.")
        return
    await state.update_data(
        description=description, description_html=message.html_text or description
    )
    await state.set_state(AdCreateStates.is_account)
    await message.answer(
        "Это аккаунт? (нужен ID для проверки)",
        reply_markup=_account_kb(),
    )


@router.message(AdCreateStates.is_account)
async def create_ad_is_account(message: Message, state: FSMContext) -> None:
    """Create ad is account.

    Args:
        message: Value for message.
        state: Value for state.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=deals_menu_kb())
        return
    text = (message.text or "").strip().lower()
    if text not in {"да", "нет"}:
        await message.answer("Выберите «Да» или «Нет».", reply_markup=_account_kb())
        return
    if text == "да":
        await state.set_state(AdCreateStates.account_id)
        await message.answer(
            "🆔 Введите ID аккаунта (для проверки базы):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await state.update_data(account_id=None)
    await state.set_state(AdCreateStates.media_type)
    await message.answer("📷 Выберите тип медиа:", reply_markup=_media_type_kb())


@router.message(AdCreateStates.account_id)
async def create_ad_account_id(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Create ad account id.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=deals_menu_kb())
        return
    account_id = (message.text or "").strip()
    if not account_id:
        await message.answer("Введите ID аккаунта.")
        return

    async with sessionmaker() as session:
        scammer = await find_scammer(session, account_id=account_id)
        if scammer:
            await state.clear()
            await message.answer(
                "⛔ Этот аккаунт найден в базе скамеров. Размещение запрещено.",
                reply_markup=deals_menu_kb(),
            )
            return
        result = await session.execute(
            select(Ad.id).where(
                Ad.account_id == account_id,
                Ad.active.is_(True),
                Ad.moderation_status != "rejected",
            )
        )
        exists = result.scalar_one_or_none()
        if exists:
            await message.answer(
                "⛔ Этот аккаунт уже есть в продаже. Размещение запрещено."
            )
            return

    await state.update_data(account_id=account_id)
    await state.set_state(AdCreateStates.media_type)
    await message.answer("📷 Выберите тип медиа:", reply_markup=_media_type_kb())


@router.message(AdCreateStates.media_type)
async def create_ad_media_type(message: Message, state: FSMContext) -> None:
    """Create ad media type.

    Args:
        message: Value for message.
        state: Value for state.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=deals_menu_kb())
        return
    text = (message.text or "").strip().lower()
    if text not in {"фото", "видео"}:
        await message.answer(
            "Выберите «Фото» или «Видео».", reply_markup=_media_type_kb()
        )
        return
    media_type = "фото" if text == "фото" else "видео"
    await state.update_data(media_type=media_type)
    await state.set_state(AdCreateStates.media)
    await message.answer("📎 Пришлите файл:", reply_markup=ReplyKeyboardRemove())


@router.message(AdCreateStates.media)
async def create_ad_media(message: Message, state: FSMContext) -> None:
    """Create ad media.

    Args:
        message: Value for message.
        state: Value for state.
    """
    data = await state.get_data()
    expected = data.get("media_type")
    if expected == "фото" and not message.photo:
        await message.answer("Нужно фото аккаунта. Отправьте фото.")
        return
    if expected == "видео" and not message.video:
        await message.answer("Нужно видео аккаунта. Отправьте видео.")
        return

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id

    if not file_id:
        await message.answer("Не удалось получить файл. Попробуйте еще раз.")
        return

    await state.update_data(media_file_id=file_id)
    await state.set_state(AdCreateStates.price)
    await message.answer("💰 Укажите цену в ₽:")


@router.message(AdCreateStates.price)
async def create_ad_price(message: Message, state: FSMContext) -> None:
    """Create ad price.

    Args:
        message: Value for message.
        state: Value for state.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=deals_menu_kb())
        return
    try:
        price = Decimal((message.text or "").replace(",", "."))
        if price <= 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        await message.answer("Введите корректную цену, например 1500.")
        return
    await state.update_data(price=price)
    await state.set_state(AdCreateStates.payment)
    await message.answer("💳 Выберите способ оплаты:", reply_markup=_payment_kb())


@router.message(AdCreateStates.payment)
async def create_ad_payment(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Create ad payment.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=deals_menu_kb())
        return
    payment = (message.text or "").strip()
    if payment not in {"Крипта", "Карта", "Крипта+Карта"}:
        await message.answer(
            "Выберите вариант оплаты из списка.", reply_markup=_payment_kb()
        )
        return

    data = await state.get_data()
    async with sessionmaker() as session:
        seller = await get_or_create_user(session, message.from_user)
        ad = Ad(
            seller_id=seller.id,
            game_id=data.get("game_id"),
            ad_kind="sale",
            title=data.get("title"),
            title_html=data.get("title_html"),
            description=data.get("description"),
            description_html=data.get("description_html"),
            account_id=data.get("account_id"),
            media_type=data.get("media_type"),
            media_file_id=data.get("media_file_id"),
            price=data.get("price"),
            payment_methods=payment,
            moderation_status="pending",
            active=True,
        )
        session.add(ad)
        await session.commit()
        result = await session.execute(select(Game).where(Game.id == ad.game_id))
        game = result.scalar_one_or_none()
        game_name = game.name if game else "-"

    await state.clear()
    account_line = ""
    if ad.account_id:
        account_line = f"🆔 ID аккаунта: {escape(ad.account_id)}\n"
    title_html = data.get("title_html") or escape(ad.title)
    description_html = data.get("description_html") or escape(ad.description)
    notify_text = (
        "🛡 Новое объявление на модерацию\n"
        f"🧾 {title_html}\n"
        f"🎮 Игра: {escape(game_name)}\n"
        f"💰 Цена: {ad.price} ₽\n"
        f"👤 Продавец: {seller.id}\n"
        f"{account_line}"
        f"🆔 ID объявления: {ad.id}\n\n"
        f"{description_html}"
    )
    await _notify_moderators(
        message.bot,
        sessionmaker,
        notify_text,
        ad.media_type,
        ad.media_file_id,
        parse_mode="HTML",
    )
    await message.answer(
        "✅ Объявление отправлено на модерацию.",
        reply_markup=deals_menu_kb(),
    )


@router.message(F.text == "🗂 Мои объявления")
async def my_ads(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Handle my ads.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Ad, Game)
            .join(Game, Game.id == Ad.game_id)
            .where(
                Ad.seller_id == message.from_user.id,
                Ad.ad_kind == "sale",
            )
            .order_by(Ad.created_at.desc())
        )
        rows = result.all()

    if not rows:
        await message.answer("Пока нет объявлений.")
        return

    for ad, game in rows:
        status = ad.moderation_status
        if status == "pending":
            status_label = "на модерации"
        elif status == "rejected":
            status_label = "отклонено"
        else:
            status_label = "активно" if ad.active else "не активно"
        title_html = ad.title_html or escape(ad.title)
        description_html = ad.description_html or escape(ad.description)
        game_name = escape(game.name)
        caption = (
            f"<b>{title_html}</b>\n"
            f"🎮 Игра: {game_name}\n"
            f"💰 Цена: {ad.price} ₽\n"
            f"📌 Статус: {status_label}\n\n"
            f"{description_html}"
        )
        await message.answer(
            caption, reply_markup=my_ad_kb(ad.id, ad.active), parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("activate:"))
async def activate_ad(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle activate ad.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    ad_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "Опубликовать объявление?",
        reply_markup=confirm_action_kb("ad_activate", ad_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ad_activate_yes:"))
async def activate_ad_yes(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle activate ad confirmation yes.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    ad_id = int(callback.data.split(":")[1])
    await _set_ad_active(callback, sessionmaker, ad_id, active=True)


@router.callback_query(F.data.startswith("ad_activate_no:"))
async def activate_ad_no(callback: CallbackQuery) -> None:
    """Handle activate ad confirmation no.

    Args:
        callback: Value for callback.
    """
    await callback.message.answer("❌ Действие отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("deactivate:"))
async def deactivate_ad(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle deactivate ad.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    ad_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "Снять объявление с публикации?",
        reply_markup=confirm_action_kb("ad_deactivate", ad_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ad_deactivate_yes:"))
async def deactivate_ad_yes(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle deactivate ad confirmation yes.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    ad_id = int(callback.data.split(":")[1])
    await _set_ad_active(callback, sessionmaker, ad_id, active=False)


@router.callback_query(F.data.startswith("ad_deactivate_no:"))
async def deactivate_ad_no(callback: CallbackQuery) -> None:
    """Handle deactivate ad confirmation no.

    Args:
        callback: Value for callback.
    """
    await callback.message.answer("❌ Действие отменено.")
    await callback.answer()


async def _set_ad_active(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    ad_id: int,
    *,
    active: bool,
) -> None:
    """Apply activation status for an ad."""
    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        if active and ad.moderation_status != "approved":
            await callback.answer("⏳ Дождитесь модерации.")
            return
        ad.active = active
        await session.commit()
    message = (
        "✅ Объявление опубликовано." if active else "✅ Объявление снято с публикации."
    )
    await callback.message.answer(message)
    await callback.answer()


@router.message(F.text == "🔁 Обмен")
async def exchange_menu(message: Message) -> None:
    """Handle exchange menu.

    Args:
        message: Value for message.
    """
    await message.answer("🔁 Раздел обмена.", reply_markup=exchange_menu_kb())


@router.message(F.text == "🗂 Все обмены")
async def exchange_list(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Handle exchange list.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    await _send_exchanges(message, sessionmaker)


@router.message(F.text == "➕ Предложить обмен")
async def exchange_offer_start(message: Message, state: FSMContext) -> None:
    """Handle exchange offer start.

    Args:
        message: Value for message.
        state: Value for state.
    """
    await state.clear()
    await state.set_state(ExchangeOfferStates.game_name)
    await message.answer(
        "🎮 Введите название игры для обмена:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ExchangeOfferStates.game_name)
async def exchange_offer_game(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle exchange offer game.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=exchange_menu_kb())
        return
    game_name = (message.text or "").strip()
    if not game_name:
        await message.answer("Введите название игры.")
        return

    async with sessionmaker() as session:
        result = await session.execute(
            select(Game).where(func.lower(Game.name) == game_name.lower())
        )
        game = result.scalar_one_or_none()
        if not game:
            game = Game(name=game_name, active=False)
            session.add(game)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(Game).where(func.lower(Game.name) == game_name.lower())
                )
                game = result.scalar_one_or_none()
            else:
                await session.refresh(game)
        if not game:
            await message.answer("Не удалось создать игру. Попробуйте позже.")
            return

    await state.update_data(game_id=game.id, game_name=game.name)
    await state.set_state(ExchangeOfferStates.want)
    await message.answer("🧩 Что хотите получить за обмен?")


@router.message(ExchangeOfferStates.want)
async def exchange_offer_want(message: Message, state: FSMContext) -> None:
    """Handle exchange offer want.

    Args:
        message: Value for message.
        state: Value for state.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=exchange_menu_kb())
        return
    want = (message.text or "").strip()
    if not want:
        await message.answer("Опишите, что хотите получить.")
        return
    await state.update_data(want=want)
    await state.set_state(ExchangeOfferStates.account_id)
    await message.answer("🆔 Введите ID аккаунта для проверки:")


@router.message(ExchangeOfferStates.account_id)
async def exchange_offer_account_id(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle exchange offer account id.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=exchange_menu_kb())
        return
    account_id = (message.text or "").strip()
    if not account_id:
        await message.answer("Введите ID аккаунта.")
        return

    async with sessionmaker() as session:
        scammer = await find_scammer(session, account_id=account_id)
        if scammer:
            await state.clear()
            await message.answer(
                "⛔ Этот аккаунт найден в базе скамеров. Размещение запрещено.",
                reply_markup=exchange_menu_kb(),
            )
            return
        result = await session.execute(
            select(Ad.id).where(
                Ad.account_id == account_id,
                Ad.active.is_(True),
                Ad.moderation_status != "rejected",
            )
        )
        exists = result.scalar_one_or_none()
        if exists:
            await message.answer(
                "⛔ Этот аккаунт уже есть в объявлениях. Размещение запрещено."
            )
            return

    await state.update_data(account_id=account_id)
    await state.set_state(ExchangeOfferStates.media_type)
    await message.answer("📷 Выберите тип медиа:", reply_markup=_media_type_kb())


@router.message(ExchangeOfferStates.media_type)
async def exchange_offer_media_type(message: Message, state: FSMContext) -> None:
    """Handle exchange offer media type.

    Args:
        message: Value for message.
        state: Value for state.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=exchange_menu_kb())
        return
    text = (message.text or "").strip().lower()
    if text not in {"фото", "видео"}:
        await message.answer(
            "Выберите «Фото» или «Видео».", reply_markup=_media_type_kb()
        )
        return
    media_type = "фото" if text == "фото" else "видео"
    await state.update_data(media_type=media_type)
    await state.set_state(ExchangeOfferStates.media)
    await message.answer("📎 Пришлите файл:", reply_markup=ReplyKeyboardRemove())


@router.message(ExchangeOfferStates.media)
async def exchange_offer_media(message: Message, state: FSMContext) -> None:
    """Handle exchange offer media.

    Args:
        message: Value for message.
        state: Value for state.
    """
    data = await state.get_data()
    expected = data.get("media_type")
    if expected == "фото" and not message.photo:
        await message.answer("Нужно фото аккаунта. Отправьте фото.")
        return
    if expected == "видео" and not message.video:
        await message.answer("Нужно видео аккаунта. Отправьте видео.")
        return

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id

    if not file_id:
        await message.answer("Не удалось получить файл. Попробуйте еще раз.")
        return

    await state.update_data(media_file_id=file_id)
    await state.set_state(ExchangeOfferStates.addon_choice)
    await message.answer("💰 Нужна доплата?", reply_markup=_yes_no_kb())


@router.message(ExchangeOfferStates.addon_choice)
async def exchange_offer_addon_choice(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle exchange offer addon choice.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=exchange_menu_kb())
        return
    text = (message.text or "").strip().lower()
    if text not in {"да", "нет"}:
        await message.answer("Выберите «Да» или «Нет».", reply_markup=_yes_no_kb())
        return
    if text == "да":
        await state.set_state(ExchangeOfferStates.addon_amount)
        await message.answer("💰 Введите сумму доплаты в ₽:")
        return

    await state.update_data(addon_amount=Decimal("0"))
    await _save_exchange_offer(message, state, sessionmaker)


@router.message(ExchangeOfferStates.addon_amount)
async def exchange_offer_addon_amount(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle exchange offer addon amount.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Создание отменено.", reply_markup=exchange_menu_kb())
        return
    try:
        addon = Decimal((message.text or "").replace(",", "."))
        if addon < 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        await message.answer("Введите корректную сумму, например 0 или 1500.")
        return
    await state.update_data(addon_amount=addon)
    await _save_exchange_offer(message, state, sessionmaker)


async def _save_exchange_offer(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle save exchange offer.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    addon_amount = data.get("addon_amount") or Decimal("0")
    addon_text = "Без доплаты" if addon_amount <= 0 else f"Доплата: {addon_amount} ₽"
    description = f"Хочу получить: {data.get('want')}\n{addon_text}"

    async with sessionmaker() as session:
        seller = await get_or_create_user(session, message.from_user)
        ad = Ad(
            seller_id=seller.id,
            game_id=data.get("game_id"),
            ad_kind="exchange",
            title=f"Обмен аккаунта {data.get('game_name')}",
            description=description,
            account_id=data.get("account_id"),
            media_type=data.get("media_type"),
            media_file_id=data.get("media_file_id"),
            price=addon_amount,
            payment_methods="обмен",
            moderation_status="pending",
            active=True,
        )
        session.add(ad)
        await session.commit()
        result = await session.execute(select(Game).where(Game.id == ad.game_id))
        game = result.scalar_one_or_none()
        game_name = game.name if game else "-"

    await state.clear()
    notify_text = (
        "🛡️ Новый обмен на модерацию\n"
        f"🔁 {ad.title}\n"
        f"🎮 Игра: {game_name}\n"
        f"💰 Доплата: {ad.price or 0} ₽\n"
        f"👤 Продавец: {seller.id}\n"
        f"🆔 ID объявления: {ad.id}"
    )
    await _notify_moderators(
        message.bot, sessionmaker, notify_text, ad.media_type, ad.media_file_id
    )
    await message.answer(
        "✅ Предложение обмена отправлено на модерацию.",
        reply_markup=exchange_menu_kb(),
    )


@router.callback_query(F.data.startswith("complaint:"))
async def complaint_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle complaint start.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    ad_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(ad_id=ad_id)
    await state.set_state(ComplaintStates.reason)
    await callback.message.answer("⚠️ Опишите причину жалобы.")
    await callback.answer()


@router.message(ComplaintStates.reason)
async def complaint_reason(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle complaint reason.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    data = await state.get_data()
    ad_id = data.get("ad_id")
    if not ad_id:
        await state.clear()
        await message.answer("Ошибка сессии.")
        return

    async with sessionmaker() as session:
        complaint = Complaint(
            ad_id=ad_id,
            reporter_id=message.from_user.id,
            reason=(message.text or "").strip(),
        )
        session.add(complaint)
        result = await session.execute(
            select(Ad, Game, User)
            .join(Game, Game.id == Ad.game_id)
            .join(User, User.id == Ad.seller_id)
            .where(Ad.id == ad_id)
        )
        row = result.first()
        ad = row[0] if row else None
        game = row[1] if row else None
        reporter = await session.get(User, complaint.reporter_id)
        if ad:
            await apply_trust_event(
                session,
                ad.seller_id,
                "complaint",
                -5,
                "Жалоба",
                ref_type="ad",
                ref_id=ad_id,
            )
        await session.commit()
        notification_text = _format_complaint_notification(
            complaint, ad, game, reporter
        )

    await state.clear()
    await message.answer("Жалоба отправлена модерации.")

    chat_id, topic_id = get_admin_target(settings)
    if chat_id:
        await message.bot.send_message(
            chat_id,
            notification_text,
            message_thread_id=topic_id,
        )
