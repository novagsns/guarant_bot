"""Module for scammers functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import Scammer, ScammerEvidence, User
from bot.handlers.helpers import get_or_create_user
from bot.keyboards.scammers import (
    public_scammers_kb,
    public_scammers_list_kb,
    scammers_list_kb,
    scammers_menu_kb,
)
from bot.utils.roles import is_owner
from bot.utils.scammers import find_scammer

router = Router()
SCAMMERS_PAGE_SIZE = 5


class ScammerCheckStates(StatesGroup):
    """Represent ScammerCheckStates.

    Attributes:
        waiting: Attribute value.
    """

    waiting = State()


class ScammerAddStates(StatesGroup):
    """Represent ScammerAddStates.

    Attributes:
        who: Attribute value.
        account_id: Attribute value.
        account_details: Attribute value.
        payment_details: Attribute value.
        notes: Attribute value.
        evidence: Attribute value.
    """

    who = State()
    account_id = State()
    account_details = State()
    payment_details = State()
    notes = State()
    evidence = State()


class ScammerRemoveStates(StatesGroup):
    """Represent ScammerRemoveStates.

    Attributes:
        waiting: Attribute value.
        confirm: Attribute value.
    """

    waiting = State()
    confirm = State()


def _is_moderator(role: str) -> bool:
    """Handle is moderator.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    return role in {"owner", "admin", "moderator"}


@router.callback_query(F.data == "info:scammers")
async def scammers_info(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle scammers info.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await callback.message.edit_text(
        "База скамеров GSNS.\nВыберите действие или фильтр списка:",
        reply_markup=public_scammers_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "scammers_public:check")
async def scammers_public_check_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Handle scammers public check start.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await state.set_state(ScammerCheckStates.waiting)
    await callback.message.answer("Введите ID или @username для проверки.")
    await callback.answer()


@router.callback_query(F.data.startswith("scammers_public:list:"))
async def scammers_public_list(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
) -> None:
    """Handle scammers public list.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    await state.clear()
    parts = callback.data.split(":") if callback.data else []
    filter_key = parts[2] if len(parts) >= 3 else "all"
    page = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 1
    offset = max(page - 1, 0) * SCAMMERS_PAGE_SIZE
    filters = []
    title = "Последние записи"
    if filter_key == "pay":
        title = "Записи с реквизитами"
        filters.append(Scammer.payment_details.isnot(None))
        filters.append(Scammer.payment_details != "")
    elif filter_key == "evidence":
        title = "Записи с доказательствами"
        filters.append(
            exists(
                select(ScammerEvidence.id).where(
                    ScammerEvidence.scammer_id == Scammer.id
                )
            )
        )

    async with sessionmaker() as session:
        stmt = (
            select(Scammer)
            .order_by(Scammer.id.desc())
            .offset(offset)
            .limit(SCAMMERS_PAGE_SIZE + 1)
        )
        if filters:
            stmt = stmt.where(*filters)
        result = await session.execute(stmt)
        scammers = result.scalars().all()
        has_more = len(scammers) > SCAMMERS_PAGE_SIZE
        if has_more:
            scammers = scammers[:SCAMMERS_PAGE_SIZE]

        evidence_counts = {}
        if scammers:
            scammer_ids = [scammer.id for scammer in scammers]
            result = await session.execute(
                select(ScammerEvidence.scammer_id, func.count().label("cnt"))
                .where(ScammerEvidence.scammer_id.in_(scammer_ids))
                .group_by(ScammerEvidence.scammer_id)
            )
            for scammer_id, cnt in result.all():
                evidence_counts[scammer_id] = cnt

    if not scammers:
        await callback.message.answer("Записей не найдено по выбранному фильтру.")
        await callback.answer()
        return

    lines = [
        f"База скамеров - {title} (страница {page}):",
    ]
    for scammer in scammers:
        name = f"@{scammer.username}" if scammer.username else "-"
        has_pay = "да" if scammer.payment_details else "нет"
        ev_count = evidence_counts.get(scammer.id, 0)
        lines.extend(
            [
                f"#{scammer.id}",
                f"ID: {scammer.user_id or '-'}",
                f"Юзернейм: {name}",
                f"ID аккаунта: {scammer.account_id or '-'}",
                f"Данные аккаунта: {scammer.account_details or '-'}",
                f"Реквизиты: {scammer.payment_details or '-'}",
                f"Примечание: {scammer.notes or '-'}",
                f"Док-ва: {ev_count} (реквизиты: {has_pay})",
                "",
            ]
        )
    await callback.message.answer(
        "\n".join(lines).rstrip(),
        reply_markup=public_scammers_list_kb(
            filter_key,
            page,
            has_more,
            [scammer.id for scammer in scammers],
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "scammers:menu")
async def scammers_menu(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammers menu.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role):
            await callback.answer("Нет доступа.")
            return
    await callback.message.answer("База скамеров:", reply_markup=scammers_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "scammers:check")
async def scammers_check_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle scammers check start.

    Args:
        callback: Value for callback.
        state: Value for state.
    """
    await state.clear()
    await state.set_state(ScammerCheckStates.waiting)
    await callback.message.answer("Введите ID или @username для проверки.")
    await callback.answer()


@router.callback_query(F.data == "guarantor:check")
async def guarantor_check_start(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle guarantor check start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if user.role not in {"guarantor", "admin", "owner"} and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return
    await state.clear()
    await state.set_state(ScammerCheckStates.waiting)
    await callback.message.answer("Введите ID или @username для проверки.")
    await callback.answer()


@router.message(ScammerCheckStates.waiting)
async def scammers_check(
    message: Message, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    """Handle scammers check.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
    """
    raw = message.text.strip()
    user_id = None
    username = None
    if raw.startswith("@"):
        username = raw[1:]
    else:
        try:
            user_id = int(raw)
        except ValueError:
            username = raw

    async with sessionmaker() as session:
        scammer = await find_scammer(session, user_id=user_id, username=username)
        if not scammer:
            await message.answer("Совпадений не найдено.")
            await state.clear()
            return

        text = (
            f"Найдено совпадение #{scammer.id}\n"
            f"ID: {scammer.user_id or '-'}\n"
            f"Юзернейм: {scammer.username or '-'}\n"
            f"ID аккаунта: {scammer.account_id or '-'}\n"
            f"Данные аккаунта: {scammer.account_details or '-'}\n"
            f"Реквизиты: {scammer.payment_details or '-'}\n"
            f"Примечание: {scammer.notes or '-'}"
        )
        await message.answer(text)

        result = await session.execute(
            select(ScammerEvidence).where(ScammerEvidence.scammer_id == scammer.id)
        )
        evidence = result.scalars().all()

    for item in evidence:
        if item.media_type == "photo":
            await message.answer_photo(item.file_id)
        elif item.media_type == "video":
            await message.answer_video(item.file_id)
        else:
            await message.answer_document(item.file_id)

    await state.clear()


@router.callback_query(F.data == "scammers:add")
async def scammers_add_start(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammers add start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return

    await state.clear()
    await state.set_state(ScammerAddStates.who)
    await callback.message.answer("Введите ID или @username (желательно указать оба):")
    await callback.answer()


@router.callback_query(F.data == "scammers:remove")
async def scammers_remove_start(
    callback: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammers remove start.

    Args:
        callback: Value for callback.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return
    await state.clear()
    await state.set_state(ScammerRemoveStates.waiting)
    await callback.message.answer(
        "Введите ID записи (#123), ID пользователя, @username или ID аккаунта:"
    )
    await callback.answer()


@router.message(ScammerRemoveStates.waiting)
async def scammers_remove(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammers remove.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Введите значение для удаления.")
        return

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_moderator(user.role):
            await state.clear()
            await message.answer("Нет доступа.")
            return

        scammer = None
        record_token = raw.lstrip("#")
        if record_token.isdigit():
            result = await session.execute(
                select(Scammer).where(Scammer.id == int(record_token))
            )
            scammer = result.scalar_one_or_none()

        if not scammer:
            if raw.isdigit():
                scammer = await find_scammer(session, user_id=int(raw))
            if not scammer:
                scammer = await find_scammer(session, username=raw)
            if not scammer:
                scammer = await find_scammer(session, account_id=raw)

        if not scammer:
            await message.answer("Запись не найдена.")
            return

    name = f"@{scammer.username}" if scammer.username else "-"
    preview = (
        f"Запись #{scammer.id}\n"
        f"ID: {scammer.user_id or '-'}\n"
        f"Юзернейм: {name}\n"
        f"ID аккаунта: {scammer.account_id or '-'}\n"
        f"Данные аккаунта: {scammer.account_details or '-'}\n"
        f"Реквизиты: {scammer.payment_details or '-'}\n"
        f"Примечание: {scammer.notes or '-'}"
    )
    await state.update_data(remove_id=scammer.id)
    await state.set_state(ScammerRemoveStates.confirm)
    await message.answer(f"{preview}\n\nУдалить запись? Ответьте Да или Нет.")


@router.message(ScammerRemoveStates.confirm)
async def scammers_remove_confirm(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammers remove confirm.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    answer = (message.text or "").strip().lower()
    if answer not in {"да", "нет"}:
        await message.answer("Ответьте Да или Нет.")
        return
    if answer == "нет":
        await state.clear()
        await message.answer("Удаление отменено.")
        return

    data = await state.get_data()
    remove_id = data.get("remove_id")
    if not remove_id:
        await state.clear()
        await message.answer("Сессия истекла.")
        return

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_moderator(user.role):
            await state.clear()
            await message.answer("Нет доступа.")
            return
        result = await session.execute(select(Scammer).where(Scammer.id == remove_id))
        scammer = result.scalar_one_or_none()
        if not scammer:
            await state.clear()
            await message.answer("Запись не найдена.")
            return
        await session.execute(
            delete(ScammerEvidence).where(ScammerEvidence.scammer_id == scammer.id)
        )
        await session.execute(delete(Scammer).where(Scammer.id == scammer.id))
        await session.commit()

    await state.clear()
    await message.answer(f"Запись #{remove_id} удалена.")


@router.callback_query(F.data == "scammers:list")
async def scammers_list(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammers list.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return
        result = await session.execute(
            select(Scammer).order_by(Scammer.id.desc()).limit(SCAMMERS_PAGE_SIZE + 1)
        )
        scammers = result.scalars().all()
        has_more = len(scammers) > SCAMMERS_PAGE_SIZE
        if has_more:
            scammers = scammers[:SCAMMERS_PAGE_SIZE]

    if not scammers:
        await callback.message.answer("Записей нет.")
        await callback.answer()
        return

    scammer_ids = [scammer.id for scammer in scammers]
    evidence_counts = {}
    async with sessionmaker() as session:
        result = await session.execute(
            select(ScammerEvidence.scammer_id, func.count().label("cnt"))
            .where(ScammerEvidence.scammer_id.in_(scammer_ids))
            .group_by(ScammerEvidence.scammer_id)
        )
        for scammer_id, cnt in result.all():
            evidence_counts[scammer_id] = cnt

    lines = ["Все записи (страница 1):"]
    for scammer in scammers:
        name = f"@{scammer.username}" if scammer.username else "-"
        ev_count = evidence_counts.get(scammer.id, 0)
        lines.extend(
            [
                f"#{scammer.id}",
                f"ID: {scammer.user_id or '-'}",
                f"Юзернейм: {name}",
                f"ID аккаунта: {scammer.account_id or '-'}",
                f"Данные аккаунта: {scammer.account_details or '-'}",
                f"Реквизиты: {scammer.payment_details or '-'}",
                f"Примечание: {scammer.notes or '-'}",
                f"Док-ва: {ev_count}",
                "",
            ]
        )
    await callback.message.answer(
        "\n".join(lines).rstrip(),
        reply_markup=scammers_list_kb(
            1, has_more, [scammer.id for scammer in scammers]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("scammers:list:"))
async def scammers_list_page(
    callback: CallbackQuery,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammers list page.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    parts = callback.data.split(":") if callback.data else []
    page = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
    offset = max(page - 1, 0) * SCAMMERS_PAGE_SIZE

    async with sessionmaker() as session:
        user = await get_or_create_user(session, callback.from_user)
        if not _is_moderator(user.role) and not is_owner(
            user.role, settings.owner_ids, user.id
        ):
            await callback.answer("Нет доступа.")
            return
        result = await session.execute(
            select(Scammer)
            .order_by(Scammer.id.desc())
            .offset(offset)
            .limit(SCAMMERS_PAGE_SIZE + 1)
        )
        scammers = result.scalars().all()
        has_more = len(scammers) > SCAMMERS_PAGE_SIZE
        if has_more:
            scammers = scammers[:SCAMMERS_PAGE_SIZE]

        scammer_ids = [scammer.id for scammer in scammers]
        evidence_counts = {}
        if scammer_ids:
            result = await session.execute(
                select(ScammerEvidence.scammer_id, func.count().label("cnt"))
                .where(ScammerEvidence.scammer_id.in_(scammer_ids))
                .group_by(ScammerEvidence.scammer_id)
            )
            for scammer_id, cnt in result.all():
                evidence_counts[scammer_id] = cnt

    if not scammers:
        await callback.message.answer("Записей нет.")
        await callback.answer()
        return

    lines = [f"Все записи (страница {page}):"]
    for scammer in scammers:
        name = f"@{scammer.username}" if scammer.username else "-"
        ev_count = evidence_counts.get(scammer.id, 0)
        lines.extend(
            [
                f"#{scammer.id}",
                f"ID: {scammer.user_id or '-'}",
                f"Юзернейм: {name}",
                f"ID аккаунта: {scammer.account_id or '-'}",
                f"Данные аккаунта: {scammer.account_details or '-'}",
                f"Реквизиты: {scammer.payment_details or '-'}",
                f"Примечание: {scammer.notes or '-'}",
                f"Док-ва: {ev_count}",
                "",
            ]
        )
    await callback.message.answer(
        "\n".join(lines).rstrip(),
        reply_markup=scammers_list_kb(
            page, has_more, [scammer.id for scammer in scammers]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("scammers_evidence:"))
async def scammers_evidence(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle scammers evidence.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    try:
        scammer_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Scammer).where(Scammer.id == scammer_id))
        scammer = result.scalar_one_or_none()
        if not scammer:
            await callback.answer("Запись не найдена.")
            return
        result = await session.execute(
            select(ScammerEvidence).where(ScammerEvidence.scammer_id == scammer.id)
        )
        evidence = result.scalars().all()

    if not evidence:
        await callback.answer("Доказательств нет.")
        return

    await callback.message.answer(f"Доказательства по записи #{scammer.id}:")
    for item in evidence:
        if item.media_type == "photo":
            await callback.message.answer_photo(item.file_id)
        elif item.media_type == "video":
            await callback.message.answer_video(item.file_id)
        else:
            await callback.message.answer_document(item.file_id)
    await callback.answer()


@router.callback_query(F.data.startswith("scammers_details:"))
async def scammers_details(
    callback: CallbackQuery, sessionmaker: async_sessionmaker
) -> None:
    """Handle scammers details.

    Args:
        callback: Value for callback.
        sessionmaker: Value for sessionmaker.
    """
    try:
        scammer_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    async with sessionmaker() as session:
        result = await session.execute(select(Scammer).where(Scammer.id == scammer_id))
        scammer = result.scalar_one_or_none()
        if not scammer:
            await callback.answer("Запись не найдена.")
            return
        result = await session.execute(
            select(ScammerEvidence.scammer_id, func.count().label("cnt"))
            .where(ScammerEvidence.scammer_id == scammer.id)
            .group_by(ScammerEvidence.scammer_id)
        )
        row = result.first()
        ev_count = row[1] if row else 0

    name = f"@{scammer.username}" if scammer.username else "-"
    text = (
        f"Запись #{scammer.id}\n"
        f"ID: {scammer.user_id or '-'}\n"
        f"Юзернейм: {name}\n"
        f"ID аккаунта: {scammer.account_id or '-'}\n"
        f"Данные аккаунта: {scammer.account_details or '-'}\n"
        f"Реквизиты: {scammer.payment_details or '-'}\n"
        f"Примечание: {scammer.notes or '-'}\n"
        f"Док-ва: {ev_count}"
    )
    await callback.message.answer(text)
    await callback.answer()


@router.message(ScammerAddStates.who)
async def scammer_add_who(message: Message, state: FSMContext) -> None:
    """Handle scammer add who.

    Args:
        message: Value for message.
        state: Value for state.
    """
    raw = message.text.strip()
    user_id = None
    username = None
    if raw.startswith("@"):
        username = raw[1:].lower()
    else:
        try:
            user_id = int(raw)
        except ValueError:
            username = raw.lower()

    await state.update_data(user_id=user_id, username=username)
    await state.set_state(ScammerAddStates.account_id)
    await message.answer("ID аккаунта (если нет — напишите пропустить):")


@router.message(ScammerAddStates.account_id)
async def scammer_add_account_id(message: Message, state: FSMContext) -> None:
    """Handle scammer add account id.

    Args:
        message: Value for message.
        state: Value for state.
    """
    value = message.text.strip()
    if value.lower() == "пропустить":
        value = None
    await state.update_data(account_id=value)
    await state.set_state(ScammerAddStates.account_details)
    await message.answer("Данные аккаунта (если нет — пропустить):")


@router.message(ScammerAddStates.account_details)
async def scammer_add_account_details(message: Message, state: FSMContext) -> None:
    """Handle scammer add account details.

    Args:
        message: Value for message.
        state: Value for state.
    """
    value = message.text.strip()
    if value.lower() == "пропустить":
        value = None
    await state.update_data(account_details=value)
    await state.set_state(ScammerAddStates.payment_details)
    await message.answer("Реквизиты (если нет — пропустить):")


@router.message(ScammerAddStates.payment_details)
async def scammer_add_payment_details(message: Message, state: FSMContext) -> None:
    """Handle scammer add payment details.

    Args:
        message: Value for message.
        state: Value for state.
    """
    value = message.text.strip()
    if value.lower() == "пропустить":
        value = None
    await state.update_data(payment_details=value)
    await state.set_state(ScammerAddStates.notes)
    await message.answer("Примечание (если нет — пропустить):")


@router.message(ScammerAddStates.notes)
async def scammer_add_notes(message: Message, state: FSMContext) -> None:
    """Handle scammer add notes.

    Args:
        message: Value for message.
        state: Value for state.
    """
    value = message.text.strip()
    if value.lower() == "пропустить":
        value = None
    await state.update_data(notes=value, evidence=[])
    await state.set_state(ScammerAddStates.evidence)
    await message.answer(
        "Отправьте доказательства (фото/видео). Можно несколько. "
        "Напишите Готово, когда закончите."
    )


@router.message(ScammerAddStates.evidence)
async def scammer_add_evidence(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle scammer add evidence.

    Args:
        message: Value for message.
        state: Value for state.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    if message.text and message.text.strip().lower() == "готово":
        data = await state.get_data()
        evidence = data.get("evidence") or []
        if not evidence:
            await message.answer("Нужно приложить хотя бы одно доказательство.")
            return

        async with sessionmaker() as session:
            user = await get_or_create_user(session, message.from_user)
            if not _is_moderator(user.role) and not is_owner(
                user.role, settings.owner_ids, user.id
            ):
                await state.clear()
                return

            scammer = Scammer(
                user_id=data.get("user_id"),
                username=data.get("username"),
                account_id=data.get("account_id"),
                account_details=data.get("account_details"),
                payment_details=data.get("payment_details"),
                notes=data.get("notes"),
                created_by=user.id,
            )
            session.add(scammer)
            await session.flush()
            for media_type, file_id in evidence:
                session.add(
                    ScammerEvidence(
                        scammer_id=scammer.id,
                        media_type=media_type,
                        file_id=file_id,
                    )
                )
            await session.commit()
            result = await session.execute(select(User.id))
            user_ids = [row[0] for row in result.all()]

        await state.clear()
        await message.answer("Скамер добавлен в базу.")
        ev_count = len(evidence)
        username_line = (
            f"Юзернейм: @{scammer.username}" if scammer.username else "Юзернейм: -"
        )
        alert_text = (
            "В базу скамеров GSNS добавлен новый аккаунт.\n"
            f"ID: {scammer.user_id or '-'}\n"
            f"{username_line}\n"
            f"ID аккаунта: {scammer.account_id or '-'}\n"
            f"Данные аккаунта: {scammer.account_details or '-'}\n"
            f"Реквизиты: {scammer.payment_details or '-'}\n"
            f"Примечание: {scammer.notes or '-'}\n"
            f"Док-ва: {ev_count}\n"
            f"Добавил: {user.id}\n\n"
            "Будьте предельно осторожны: совершайте сделки только через GSNS, "
            "чтобы избежать потери денег или аккаунта."
        )
        for user_id in user_ids:
            try:
                await message.bot.send_message(user_id, alert_text)
            except Exception:
                continue
        return

    file_id = None
    media_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"

    if not file_id:
        await message.answer("Нужны фото или видео.")
        return

    data = await state.get_data()
    evidence = data.get("evidence") or []
    evidence.append((media_type, file_id))
    await state.update_data(evidence=evidence)
    await message.answer("Доказательство добавлено. Пришлите еще или Готово.")
