"""Module for scammers functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def scammers_menu_kb() -> InlineKeyboardMarkup:
    """Handle scammers menu kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Проверить", callback_data="scammers:check"),
                InlineKeyboardButton(text="Добавить", callback_data="scammers:add"),
            ],
            [
                InlineKeyboardButton(text="Список", callback_data="scammers:list"),
                InlineKeyboardButton(text="Удалить", callback_data="scammers:remove"),
            ],
        ]
    )


def scammers_list_kb(
    page: int, has_more: bool, scammer_ids: list[int] | None = None
) -> InlineKeyboardMarkup:
    """Handle scammers list kb.

    Args:
        page: Value for page.
        has_more: Value for has_more.
        scammer_ids: Value for scammer_ids.

    Returns:
        Return value.
    """
    buttons = []
    for scammer_id in scammer_ids or []:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"Подробнее #{scammer_id}",
                    callback_data=f"scammers_details:{scammer_id}",
                ),
                InlineKeyboardButton(
                    text=f"Доказательства #{scammer_id}",
                    callback_data=f"scammers_evidence:{scammer_id}",
                ),
            ]
        )
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад", callback_data=f"scammers:list:{page - 1}"
            )
        )
    if has_more:
        nav.append(
            InlineKeyboardButton(
                text="Вперед ➡️", callback_data=f"scammers:list:{page + 1}"
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="Меню", callback_data="scammers:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def public_scammers_list_kb(
    filter_key: str,
    page: int,
    has_more: bool,
    scammer_ids: list[int] | None = None,
) -> InlineKeyboardMarkup:
    """Handle public scammers list kb.

    Args:
        filter_key: Value for filter_key.
        page: Value for page.
        has_more: Value for has_more.
        scammer_ids: Value for scammer_ids.

    Returns:
        Return value.
    """
    buttons = []
    for scammer_id in scammer_ids or []:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"Подробнее #{scammer_id}",
                    callback_data=f"scammers_details:{scammer_id}",
                ),
                InlineKeyboardButton(
                    text=f"Доказательства #{scammer_id}",
                    callback_data=f"scammers_evidence:{scammer_id}",
                ),
            ]
        )
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"scammers_public:list:{filter_key}:{page - 1}",
            )
        )
    if has_more:
        nav.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=f"scammers_public:list:{filter_key}:{page + 1}",
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text="Фильтры", callback_data="info:scammers")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def public_scammers_kb() -> InlineKeyboardMarkup:
    """Handle public scammers kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Проверить",
                    callback_data="scammers_public:check",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Список: все",
                    callback_data="scammers_public:list:all:1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Список: с реквизитами",
                    callback_data="scammers_public:list:pay:1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Список: с доказательствами",
                    callback_data="scammers_public:list:evidence:1",
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data="info:back")],
        ]
    )
