"""Module for ads functionality."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import Ad, Complaint, Game, User
from bot.handlers.helpers import get_or_create_user
from bot.services.trust import apply_trust_event
from bot.keyboards.ads import (
    ad_actions_kb,
    account_filter_kb,
    exchange_actions_kb,
    game_list_kb,
    my_ad_kb,
)
from bot.keyboards.common import deals_menu_kb, exchange_menu_kb
from bot.utils.scammers import find_scammer
from bot.utils.vip import is_vip_until

router = Router()


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

    for mod in moderators:
        if media_type == "фото" and media_file_id:
            await bot.send_photo(mod.id, media_file_id, caption=text)
        elif media_type == "видео" and media_file_id:
            await bot.send_video(mod.id, media_file_id, caption=text)
        else:
            await bot.send_message(mod.id, text)


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


async def _send_ads(
    message: Message,
    sessionmaker: async_sessionmaker,
    *,
    ad_kind: str,
) -> None:
    """Handle send ads.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        ad_kind: Value for ad_kind.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Ad, Game)
            .join(Game, Game.id == Ad.game_id)
            .where(
                Ad.active.is_(True),
                Ad.moderation_status == "approved",
                Ad.ad_kind == ad_kind,
            )
            .order_by(Ad.created_at.desc())
            .limit(20)
        )
        rows = result.all()

    if not rows:
        empty_text = (
            "Пока нет активных предложений обмена."
            if ad_kind == "exchange"
            else "Пока нет объявлений."
        )
        await message.answer(empty_text)
        return

    for ad, game in rows:
        if ad_kind == "exchange":
            price_line = f"💰 Доплата: {ad.price or 0} ₽\n"
            actions_kb = exchange_actions_kb(ad.id)
        else:
            price_line = f"💰 Цена: {ad.price} ₽\n"
            actions_kb = ad_actions_kb(ad.id)

        caption = (
            f"<b>{ad.title}</b>\n"
            f"🎮 Игра: {game.name}\n"
            f"{price_line}"
            f"{ad.description}"
        )

        if ad.media_type == "фото" and ad.media_file_id:
            await message.answer_photo(
                ad.media_file_id, caption=caption, reply_markup=actions_kb
            )
        elif ad.media_type == "видео" and ad.media_file_id:
            await message.answer_video(
                ad.media_file_id, caption=caption, reply_markup=actions_kb
            )
        else:
            await message.answer(caption, reply_markup=actions_kb)


async def _send_exchanges(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Handle send exchanges.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    await _send_ads(message, sessionmaker, ad_kind="exchange")


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
    await _send_ads(message, sessionmaker, ad_kind="sale")


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
    await state.update_data(title=title)
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
    await state.update_data(description=description)
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
            description=data.get("description"),
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
    notify_text = (
        "🛡️ Новое объявление на модерацию\n"
        f"🧾 {ad.title}\n"
        f"🎮 Игра: {game_name}\n"
        f"💰 Цена: {ad.price} ₽\n"
        f"👤 Продавец: {seller.id}\n"
        f"🆔 ID объявления: {ad.id}"
    )
    await _notify_moderators(
        message.bot, sessionmaker, notify_text, ad.media_type, ad.media_file_id
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
        caption = (
            f"<b>{ad.title}</b>\n"
            f"🎮 Игра: {game.name}\n"
            f"💰 Цена: {ad.price} ₽\n"
            f"📌 Статус: {status_label}\n\n"
            f"{ad.description}"
        )
        await message.answer(caption, reply_markup=my_ad_kb(ad.id, ad.active))


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
    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        if ad.moderation_status != "approved":
            await callback.answer("⏳ Дождитесь модерации.")
            return
        ad.active = True
        await session.commit()
    await callback.message.answer("✅ Объявление опубликовано.")
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
    async with sessionmaker() as session:
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
        if not ad or ad.seller_id != callback.from_user.id:
            await callback.answer("Нет доступа.")
            return
        ad.active = False
        await session.commit()
    await callback.message.answer("✅ Объявление снято с публикации.")
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
            game = Game(name=game_name)
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
            await message.answer("?? ??????? ???????? ????. ?????????? ?????.")
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
        result = await session.execute(select(Ad).where(Ad.id == ad_id))
        ad = result.scalar_one_or_none()
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

    await state.clear()
    await message.answer("Жалоба отправлена модерации.")
