"""Module for start functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import (
    Ad,
    ModerationChat,
    ModerationRestriction,
    Review,
    User,
    WalletTransaction,
)
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.common import (
    deals_menu_kb,
    main_menu_kb,
    referral_kb,
    tools_fee_type_kb,
    tools_menu_kb,
)
from bot.services.fees import calculate_fee
from bot.services.trust import apply_trust_event, get_trust_score
from bot.services.trade_bonus import get_trade_level
from bot.services.weekly_rewards import grant_pending_rewards
from bot.utils.roles import is_owner
from bot.utils.scammers import find_scammer
from bot.utils.texts import TOOLS_TEXT, WELCOME_TEXT

router = Router()


TRADE_BONUS_ANNOUNCEMENT = (
    "📣 GSNS — ОБНОВЛЕНИЕ ДЛЯ ПЕРЕКУПОВ / ТРЕЙДЕРОВ\n\n"
    "Запускаем бонусную систему по количеству сделок.\n\n"
    "⚠️ ВАЖНО:\n"
    "• считаются только сделки, проведённые через бота GSNS Trade ✅\n"
    "• в зачёт идут сделки на сумму от 2500 ₽ ✅\n\n"
    "────────────────────\n"
    "📌 БАЗОВЫЕ УСЛОВИЯ GSNS\n\n"
    "К/П (купля / продажа):\n"
    "• < 2000 ₽ → 250 ₽\n"
    "• 2000–24999 ₽ → 12%\n"
    "• ≥ 25000 ₽ → 10%\n\n"
    "Обмен:\n"
    "• обмен → 400 ₽\n"
    "• обмен с доплатой → 400 ₽ + 10% от доплаты\n\n"
    "Рассрочка:\n"
    "• 14%\n"
    "────────────────────\n"
    "🎁 БОНУСЫ ПО УРОВНЯМ (для перекупов/трейдеров)\n\n"
    "🥉 УРОВЕНЬ 1 — 18+ сделок\n"
    "• К/П: 10%\n"
    "• обмен: 350 ₽\n"
    "• обмен с доплатой: 350 ₽ + 8% от доплаты\n"
    "🏷 Префикс: «GSNS Trader»\n\n"
    "🥈 УРОВЕНЬ 2 — 25+ сделок\n"
    "• К/П: 9%\n"
    "• обмен: 300 ₽\n"
    "• обмен с доплатой: 300 ₽ + 7% от доплаты\n"
    "• рассрочка: 13%\n"
    "🏷 Префикс: «GSNS PRO»\n\n"
    "📌 С префиксом «GSNS PRO» скидка действует и для покупателя,\n"
    "если у покупателя тоже есть префикс «GSNS PRO».\n\n"
    "🥇 УРОВЕНЬ 3 — 40+ сделок\n"
    "• К/П: 8%\n"
    "• обмен: 250 ₽\n"
    "• обмен с доплатой: 250 ₽ + 5% от доплаты\n"
    "• рассрочка: 11%\n"
    "🏷 Префикс: «GSNS ELITE»"
)


class ToolsStates(StatesGroup):
    """Represent ToolsStates.

    Attributes:
        account_id: Attribute value.
        fee_amount: Attribute value.
        fee_addon: Attribute value.
    """

    account_id = State()
    fee_amount = State()
    fee_addon = State()


def _format_until(value) -> str:
    """Handle format until.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    if not value:
        return "-"
    try:
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "-"


def _build_main_menu_markup(user: User, settings: Settings):
    role = user.role or "user"
    return main_menu_kb(
        role,
        is_owner=is_owner(role, settings.owner_ids, user.id),
    )


async def _sync_user_restrictions(
    bot,
    sessionmaker: async_sessionmaker,
    user_id: int,
) -> None:
    """Handle sync user restrictions.

    Args:
        bot: Value for bot.
        sessionmaker: Value for sessionmaker.
        user_id: Value for user_id.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationChat).where(ModerationChat.active.is_(True))
        )
        chats = result.scalars().all()

    for chat in chats:
        # Pull live chat membership state to keep restrictions in sync.
        try:
            member = await bot.get_chat_member(chat.chat_id, user_id)
        except Exception:
            continue

        action = None
        until_date = None
        status = getattr(member, "status", None)
        if status == "kicked":
            action = "ban"
        elif status == "restricted":
            can_send = getattr(member, "can_send_messages", True)
            if can_send is False:
                action = "mute"
                until_date = getattr(member, "until_date", None)

        async with sessionmaker() as session:
            # Persist current restriction state and apply trust impact once.
            if action:
                result = await session.execute(
                    select(ModerationRestriction).where(
                        ModerationRestriction.chat_id == chat.chat_id,
                        ModerationRestriction.user_id == user_id,
                        ModerationRestriction.action == action,
                        ModerationRestriction.active.is_(True),
                    )
                )
                record = result.scalar_one_or_none()
                if record:
                    record.until_date = until_date
                    await session.commit()
                else:
                    record = ModerationRestriction(
                        chat_id=chat.chat_id,
                        user_id=user_id,
                        action=action,
                        reason="\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430",
                        until_date=until_date,
                        active=True,
                    )
                    session.add(record)
                    await session.commit()
                    if action == "ban":
                        await apply_trust_event(
                            session,
                            user_id,
                            "chat_ban",
                            -20,
                            record.reason
                            or "\u0411\u0430\u043d \u0432 \u0447\u0430\u0442\u0435",
                            ref_type="restriction",
                            ref_id=record.id,
                        )
                    elif action == "mute":
                        await apply_trust_event(
                            session,
                            user_id,
                            "chat_mute",
                            -5,
                            record.reason
                            or "\u041c\u0443\u0442 \u0432 \u0447\u0430\u0442\u0435",
                            ref_type="restriction",
                            ref_id=record.id,
                        )
            else:
                result = await session.execute(
                    select(ModerationRestriction).where(
                        ModerationRestriction.chat_id == chat.chat_id,
                        ModerationRestriction.user_id == user_id,
                        ModerationRestriction.active.is_(True),
                    )
                )
                for record in result.scalars().all():
                    record.active = False
                await session.commit()


async def _send_restrictions_summary(
    message: Message, sessionmaker: async_sessionmaker
) -> bool:
    """Handle send restrictions summary.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(ModerationRestriction, ModerationChat)
            .join(
                ModerationChat,
                ModerationChat.chat_id == ModerationRestriction.chat_id,
                isouter=True,
            )
            .where(
                ModerationRestriction.user_id == message.from_user.id,
                ModerationRestriction.active.is_(True),
            )
        )
        rows = result.all()

    if not rows:
        return False

    lines = [
        "\u26a0\ufe0f \u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u044f \u0432 \u043c\u043e\u0434\u0435\u0440\u0438\u0440\u0443\u0435\u043c\u044b\u0445 \u0447\u0430\u0442\u0430\u0445:"
    ]
    for record, chat in rows:
        chat_title = chat.title if chat and chat.title else str(record.chat_id)
        action_label = (
            "\u0411\u0430\u043d" if record.action == "ban" else "\u041c\u0443\u0442"
        )
        until_label = (
            f", \u0434\u043e {_format_until(record.until_date)}"
            if record.action == "mute"
            else ""
        )
        reason = (
            record.reason
            or "\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430"
        )
        lines.append(
            f"{action_label} | {chat_title}{until_label}\n\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason}"
        )
    await message.answer("\n\n".join(lines))
    return True


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle cmd start.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    await state.clear()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        created = False
        if not user:
            created = True
            referrer_id = None
            if message.text and len(message.text.split()) > 1:
                try:
                    referrer_id = int(message.text.split()[1])
                except ValueError:
                    referrer_id = None
            user = User(
                id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                referrer_id=referrer_id,
            )
            session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                # Another concurrent /start created the row.
                await session.rollback()
                created = False
                result = await session.execute(
                    select(User).where(User.id == message.from_user.id)
                )
                user = result.scalar_one_or_none()
        else:
            await get_or_create_user(session, message.from_user)

        if created and user.referrer_id:
            result = await session.execute(
                select(User).where(User.id == user.referrer_id)
            )
            referrer = result.scalar_one_or_none()
            if referrer:
                referrer.balance = (referrer.balance or 0) + settings.referral_bonus
                session.add(
                    WalletTransaction(
                        user_id=referrer.id,
                        amount=settings.referral_bonus,
                        type="referral",
                        description=f"Реферал {user.id}",
                    )
                )
                await session.commit()
        users_count = await session.execute(select(func.count(User.id)))
        reviews_count = await session.execute(
            select(func.count(Review.id))
            .select_from(Review)
            .join(User, User.id == Review.target_id)
            .where(User.role == "guarantor")
        )

    users_total = users_count.scalar_one() or 0
    reviews_total = reviews_count.scalar_one() or 0
    trust_block = (
        f"\n\nНам доверяют: {users_total} пользователей.\n"
        f"Отзывы о гарантах: {reviews_total} отзывов."
    )
    await message.answer(
        f"{WELCOME_TEXT}{trust_block}",
        reply_markup=_build_main_menu_markup(user, settings),
    )
    await _sync_user_restrictions(message.bot, sessionmaker, message.from_user.id)
    await _send_restrictions_summary(message, sessionmaker)
    await message.answer(
        "🎁 Донат с выгодным курсом для вашей игры:",
        reply_markup=referral_kb(),
    )
    await grant_pending_rewards(message.bot, sessionmaker, message.from_user.id)


@router.message(F.text == "/id")
async def cmd_id(message: Message) -> None:
    """Handle cmd id.

    Args:
        message: Value for message.
    """
    thread_id = message.message_thread_id
    text = (
        f"CHAT_ID: {message.chat.id}\n"
        f"TOPIC_ID: {thread_id if thread_id is not None else 'нет'}"
    )
    await message.answer(text)


@router.message(F.text == "📦 Сделки и объявления")
async def menu_deals(message: Message) -> None:
    """Handle menu deals.

    Args:
        message: Value for message.
    """
    await message.answer("Выберите действие.", reply_markup=deals_menu_kb())


@router.message(F.text == "⬅️ Назад")
async def menu_back(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle menu back.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
    await message.answer(
        "Главное меню.",
        reply_markup=_build_main_menu_markup(user, settings),
    )


@router.message(F.text == "🧰 Инструменты")
async def menu_tools(message: Message) -> None:
    """Handle menu tools.

    Args:
        message: Value for message.
    """
    await message.answer(TOOLS_TEXT, reply_markup=tools_menu_kb())


@router.callback_query(F.data == "tools:back")
async def tools_back(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle tools back.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    await state.clear()
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
    await callback.message.answer(
        "\u0413\u043b\u0430\u0432\u043d\u043e\u0435 \u043c\u0435\u043d\u044e.",
        reply_markup=_build_main_menu_markup(user, settings),
    )
    await callback.answer()


@router.callback_query(F.data == "tools:restrictions")
async def tools_restrictions(
    callback: CallbackQuery, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle tools restrictions.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    await state.clear()
    shown = await _send_restrictions_summary(callback.message, sessionmaker)
    if not shown:
        await callback.message.answer(
            "\u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0439 \u043d\u0435\u0442."
        )
    await callback.answer()


@router.callback_query(F.data == "tools:account_check")
async def tools_account_check_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle tools account check start.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await state.set_state(ToolsStates.account_id)
    await callback.message.answer(
        "\U0001f194 \u0412\u0432\u0435\u0434\u0438\u0442\u0435 ID \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430 \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438."
    )
    await callback.answer()


@router.message(ToolsStates.account_id)
async def tools_account_check(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle tools account check.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    account_id = (message.text or "").strip()
    if not account_id:
        await message.answer(
            "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 ID \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430."
        )
        return
    async with sessionmaker() as session:
        scammer = await find_scammer(session, account_id=account_id)
        if scammer:
            name = f"@{scammer.username}" if scammer.username else "-"
            text = (
                f"\u041d\u0430\u0439\u0434\u0435\u043d\u043e \u0441\u043e\u0432\u043f\u0430\u0434\u0435\u043d\u0438\u0435 #{scammer.id}\n"
                f"ID: {scammer.user_id or '-'}\n"
                f"\u042e\u0437\u0435\u0440\u043d\u0435\u0439\u043c: {name}\n"
                f"ID \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430: {scammer.account_id or '-'}\n"
                f"\u0414\u0430\u043d\u043d\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430: {scammer.account_details or '-'}\n"
                f"\u0420\u0435\u043a\u0432\u0438\u0437\u0438\u0442\u044b: {scammer.payment_details or '-'}\n"
                f"\u041f\u0440\u0438\u043c\u0435\u0447\u0430\u043d\u0438\u0435: {scammer.notes or '-'}"
            )
            await state.clear()
            await message.answer(text)
            return
        result = await session.execute(
            select(Ad.id).where(
                Ad.account_id == account_id,
                Ad.active.is_(True),
                Ad.moderation_status != "rejected",
            )
        )
        exists = result.scalar_one_or_none()
    await state.clear()
    if exists:
        await message.answer(
            "\u26a0\ufe0f \u042d\u0442\u043e\u0442 \u0430\u043a\u043a\u0430\u0443\u043d\u0442 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0432 \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u044f\u0445."
        )
    else:
        await message.answer(
            "\u2705 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0432 \u0431\u0430\u0437\u0435 \u0441\u043a\u0430\u043c\u0435\u0440\u043e\u0432."
        )


@router.callback_query(F.data == "tools:fee")
async def tools_fee_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle tools fee start.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await callback.message.answer(
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0442\u0438\u043f \u0441\u0434\u0435\u043b\u043a\u0438:",
        reply_markup=tools_fee_type_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tools:fee_type:"))
async def tools_fee_type(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle tools fee type.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    parts = callback.data.split(":") if callback.data else []
    fee_type = parts[2] if len(parts) >= 3 else "sale"
    await state.clear()
    if fee_type == "exchange":
        fee = calculate_fee("0", "exchange")
        await callback.message.answer(
            f"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f \u0437\u0430 \u043e\u0431\u043c\u0435\u043d: {fee} \u20bd"
        )
        await callback.answer()
        return
    await state.update_data(fee_type=fee_type)
    if fee_type == "exchange_with_addon":
        await state.set_state(ToolsStates.fee_addon)
        await callback.message.answer(
            "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0441\u0443\u043c\u043c\u0443 \u0434\u043e\u043f\u043b\u0430\u0442\u044b."
        )
    else:
        await state.set_state(ToolsStates.fee_amount)
        await callback.message.answer(
            "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0441\u0443\u043c\u043c\u0443 \u0441\u0434\u0435\u043b\u043a\u0438."
        )
    await callback.answer()


@router.callback_query(F.data == "tools:trade_bonus")
async def tools_trade_bonus(callback: CallbackQuery) -> None:
    """Show the trade bonus announcement."""

    await callback.answer()
    await callback.message.answer(TRADE_BONUS_ANNOUNCEMENT)


@router.message(Command("trade_bonus"))
async def trade_bonus_message(message: Message) -> None:
    """Handle /trade_bonus command."""

    await message.answer(TRADE_BONUS_ANNOUNCEMENT)


@router.message(ToolsStates.fee_amount)
async def tools_fee_amount(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle tools fee amount.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    raw_amount = (message.text or "").strip().replace(",", ".")
    if not raw_amount or raw_amount.count(".") > 1:
        await message.answer(
            "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0441\u0443\u043c\u043c\u0443."
        )
        return
    data = await state.get_data()
    fee_type = data.get("fee_type", "sale")
    async with sessionmaker() as session:
        trust_score = await get_trust_score(session, message.from_user.id)
        trade_level = await get_trade_level(session, message.from_user.id)
    fee = calculate_fee(
        raw_amount,
        fee_type,
        trust_score=trust_score,
        trade_level=trade_level,
    )
    await state.clear()
    if fee is None:
        await message.answer(
            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0442\u044c \u043a\u043e\u043c\u0438\u0441\u0441\u0438\u044e."
        )
        return
    await message.answer(
        f"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f: {fee} \u20bd"
    )


@router.message(ToolsStates.fee_addon)
async def tools_fee_addon(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle tools fee addon.

    Args:
        message: Value for message.
        state: Value for state.
    """
    raw_amount = (message.text or "").strip().replace(",", ".")
    if not raw_amount or raw_amount.count(".") > 1:
        await message.answer(
            "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0441\u0443\u043c\u043c\u0443 \u0434\u043e\u043f\u043b\u0430\u0442\u044b."
        )
        return
    async with sessionmaker() as session:
        trade_level = await get_trade_level(session, message.from_user.id)
    fee = calculate_fee(
        "0",
        "exchange_with_addon",
        addon_amount=raw_amount,
        trade_level=trade_level,
    )
    await state.clear()
    if fee is None:
        await message.answer(
            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0442\u044c \u043a\u043e\u043c\u0438\u0441\u0441\u0438\u044e."
        )
        return
    await message.answer(
        f"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f: {fee} \u20bd"
    )


@router.message(F.text == "/cancel")
@router.message(F.text == "Отмена")
async def cancel_flow(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle cancel flow.

    Args:
        message: Value for message.
        state: Value for state.
    """
    await state.clear()
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
    await message.answer(
        "✅ Действие отменено.",
        reply_markup=_build_main_menu_markup(user, settings),
    )
