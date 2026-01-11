# -*- coding: utf-8 -*-
"""Ad alert subscription handlers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import Game
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.ads import game_list_kb
from bot.services.ad_alerts import MAX_SUBSCRIPTIONS, create_subscription, delete_subscription, list_subscriptions

router = Router()

PRIVATE_ONLY_TEXT = "Команда доступна только в ЛС."


async def _ensure_private_message(message: Message) -> bool:
    if message.chat.type != "private":
        await message.answer(PRIVATE_ONLY_TEXT)
        return False
    return True


async def _ensure_private_callback(callback: CallbackQuery) -> bool:
    if not callback.message or callback.message.chat.type != "private":
        await callback.answer(PRIVATE_ONLY_TEXT, show_alert=True)
        return False
    return True


class AdAlertStates(StatesGroup):
    """Subscription creation states."""

    game_id = State()
    price_min = State()
    price_max = State()
    server_query = State()


def _alerts_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить подписку", callback_data="alert_add")],
            [InlineKeyboardButton(text="🗑 Удалить подписку", callback_data="alert_delete_menu")],
        ]
    )


def _format_price(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}".replace(",", " ")


def _subscription_line(sub: dict, game_names: dict[int, str]) -> str:
    game_id = sub.get("game_id")
    game_label = game_names.get(game_id, "Любая игра") if game_id else "Любая игра"
    price_min = sub.get("price_min")
    price_max = sub.get("price_max")
    if price_min is None and price_max is None:
        price_label = "любая цена"
    elif price_min is not None and price_max is not None:
        price_label = f"{_format_price(price_min)}–{_format_price(price_max)} ₽"
    elif price_min is not None:
        price_label = f"от {_format_price(price_min)} ₽"
    else:
        price_label = f"до {_format_price(price_max)} ₽"
    server_query = sub.get("server_query") or "любой"
    return f"#{sub['id']} • {game_label} • {price_label} • сервер: {server_query}"


async def _render_subscriptions(
    message: Message, sessionmaker: async_sessionmaker
) -> list[dict]:
    subs = await list_subscriptions(sessionmaker, message.from_user.id)
    if not subs:
        await message.answer(
            "🔔 Подписок пока нет.\n"
            "Нажмите «Добавить подписку», чтобы получать уведомления о новых объявлениях.",
            reply_markup=_alerts_menu_kb(),
        )
        return []

    game_ids = {sub["game_id"] for sub in subs if sub.get("game_id")}
    game_names: dict[int, str] = {}
    if game_ids:
        async with sessionmaker() as session:
            result = await session.execute(
                select(Game.id, Game.name).where(Game.id.in_(game_ids))
            )
            game_names = {row[0]: row[1] for row in result.all()}

    lines = ["🔔 <b>Ваши подписки на объявления</b>:"]
    for sub in subs:
        lines.append(_subscription_line(sub, game_names))
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=_alerts_menu_kb())
    return subs


def _parse_price_input(text: str | None) -> Decimal | None:
    if not text:
        return None
    raw = text.strip().replace(",", ".").lower()
    if raw in {"0", "-", "нет"}:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if value < 0:
        return None
    return value


@router.message(Command("alerts"))
async def alerts_menu(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Show alert subscriptions menu."""
    if not await _ensure_private_message(message):
        return
    async with sessionmaker() as session:
        await get_or_create_user(session, message.from_user)
    await _render_subscriptions(message, sessionmaker)


@router.callback_query(F.data == "alert_add")
async def alert_add(callback: CallbackQuery, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    if not await _ensure_private_callback(callback):
        return
    await state.clear()
    await state.set_state(AdAlertStates.game_id)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Game.id, Game.name).where(Game.active.is_(True)).order_by(Game.name)
        )
        games = result.all()
    if not games:
        await callback.message.answer("Список игр пуст. Обратитесь к администратору.")
        await callback.answer()
        return
    await callback.message.answer(
        "🎮 Выберите игру для подписки:",
        reply_markup=game_list_kb(games, prefix="alert_game", include_all=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alert_game:"))
async def alert_game_selected(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_private_callback(callback):
        return
    raw = callback.data.split(":", 1)[1]
    try:
        game_id = int(raw)
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    await state.update_data(game_id=game_id if game_id > 0 else None)
    await state.set_state(AdAlertStates.price_min)
    await callback.message.answer("💰 Минимальная цена (в ₽). Напишите 0, если не важно.")
    await callback.answer()


@router.message(AdAlertStates.price_min)
async def alert_price_min(message: Message, state: FSMContext) -> None:
    if not await _ensure_private_message(message):
        return
    value = _parse_price_input(message.text)
    if message.text and value is None and message.text.strip() not in {"0", "-", "нет"}:
        await message.answer("Введите корректную сумму, например 0 или 1500.")
        return
    await state.update_data(price_min=value)
    await state.set_state(AdAlertStates.price_max)
    await message.answer("💰 Максимальная цена (в ₽). Напишите 0, если не важно.")


@router.message(AdAlertStates.price_max)
async def alert_price_max(message: Message, state: FSMContext) -> None:
    if not await _ensure_private_message(message):
        return
    value = _parse_price_input(message.text)
    if message.text and value is None and message.text.strip() not in {"0", "-", "нет"}:
        await message.answer("Введите корректную сумму, например 0 или 1500.")
        return
    data = await state.get_data()
    price_min = data.get("price_min")
    if price_min is not None and value is not None and value < price_min:
        await message.answer("Максимальная цена не может быть меньше минимальной. Введите снова.")
        return
    await state.update_data(price_max=value)
    await state.set_state(AdAlertStates.server_query)
    await message.answer(
        "🌍 Сервер/регион (ключевые слова).\n"
        "Например: EU, NA, RU. Если не нужно — напишите 0."
    )


@router.message(AdAlertStates.server_query)
async def alert_server_query(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    if not await _ensure_private_message(message):
        return
    raw = (message.text or "").strip()
    server_query = None
    if raw and raw not in {"0", "-", "нет"}:
        server_query = raw[:64]
    data = await state.get_data()
    game_id = data.get("game_id")
    price_min = data.get("price_min")
    price_max = data.get("price_max")

    sub_id = await create_subscription(
        sessionmaker,
        user_id=message.from_user.id,
        game_id=game_id,
        price_min=price_min,
        price_max=price_max,
        server_query=server_query,
    )
    await state.clear()
    if sub_id is None:
        await message.answer(
            f"Достигнут лимит подписок ({MAX_SUBSCRIPTIONS}). "
            "Удалите лишние подписки и попробуйте снова."
        )
        return
    await message.answer("✅ Подписка создана.")
    await _render_subscriptions(message, sessionmaker)


@router.callback_query(F.data == "alert_delete_menu")
async def alert_delete_menu(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    if not await _ensure_private_callback(callback):
        return
    subs = await list_subscriptions(sessionmaker, callback.from_user.id)
    if not subs:
        await callback.message.answer("Подписок нет.")
        await callback.answer()
        return
    rows = []
    row: list[InlineKeyboardButton] = []
    for idx, sub in enumerate(subs, start=1):
        row.append(
            InlineKeyboardButton(
                text=f"❌ #{sub['id']}", callback_data=f"alert_del:{sub['id']}"
            )
        )
        if idx % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="alert_back")])
    await callback.message.answer(
        "Выберите подписку для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


@router.callback_query(F.data == "alert_back")
async def alert_back(callback: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    if not await _ensure_private_callback(callback):
        return
    await _render_subscriptions(callback.message, sessionmaker)
    await callback.answer()


@router.callback_query(F.data.startswith("alert_del:"))
async def alert_delete(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    if not await _ensure_private_callback(callback):
        return
    raw = callback.data.split(":", 1)[1]
    try:
        sub_id = int(raw)
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    deleted = await delete_subscription(
        sessionmaker, user_id=callback.from_user.id, sub_id=sub_id
    )
    if deleted:
        await callback.message.answer("✅ Подписка удалена.")
    else:
        await callback.message.answer("Подписка не найдена.")
    await _render_subscriptions(callback.message, sessionmaker)
    await callback.answer()
