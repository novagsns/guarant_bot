"""Module for services functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def services_menu_kb(is_admin: bool, roulette_cost: str) -> InlineKeyboardMarkup:
    """Handle services menu kb.

    Args:
        is_admin: Value for is_admin.
        roulette_cost: Value for roulette_cost.

    Returns:
        Return value.
    """
    rows = [
        [
            InlineKeyboardButton(text="Эксклюзив", callback_data="services:exclusive"),
            InlineKeyboardButton(text="Аккаунты", callback_data="services:accounts"),
        ],
        [InlineKeyboardButton(text="Услуги", callback_data="services:services")],
        [
            InlineKeyboardButton(
                text=f"🎰 Испытай удачу ({roulette_cost} GSNS)",
                callback_data="roulette:start",
            )
        ],
        [
            InlineKeyboardButton(
                text="Пополнить GSNS Coins", callback_data="topup:start"
            )
        ],
    ]
    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Добавить услугу", callback_data="services:add"
                ),
                InlineKeyboardButton(text="Мои услуги", callback_data="services:mine"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_list_kb(services: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Handle service list kb.

    Args:
        services: Value for services.

    Returns:
        Return value.
    """
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"service:{service_id}")]
        for service_id, title in services
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_buy_kb(service_id: int) -> InlineKeyboardMarkup:
    """Handle service buy kb.

    Args:
        service_id: Value for service_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Купить", callback_data=f"service_buy:{service_id}"
                )
            ]
        ]
    )


def my_service_kb(service_id: int) -> InlineKeyboardMarkup:
    """Handle my service kb.

    Args:
        service_id: Value for service_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Редактировать", callback_data=f"service_edit:{service_id}"
                ),
                InlineKeyboardButton(
                    text="Медиа", callback_data=f"service_media:{service_id}"
                ),
                InlineKeyboardButton(
                    text="Удалить", callback_data=f"service_delete:{service_id}"
                ),
            ]
        ]
    )


def service_chat_kb(purchase_id: int) -> InlineKeyboardMarkup:
    """Handle service chat kb.

    Args:
        purchase_id: Value for purchase_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Чат", callback_data=f"service_chat:{purchase_id}"
                )
            ]
        ]
    )


def topup_review_kb(topup_id: int) -> InlineKeyboardMarkup:
    """Handle topup review kb.

    Args:
        topup_id: Value for topup_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, подтвердить", callback_data=f"topup_ok:{topup_id}"
                ),
                InlineKeyboardButton(
                    text="Нет, отклонить", callback_data=f"topup_reject:{topup_id}"
                ),
            ]
        ]
    )


def topup_reject_reason_kb(topup_id: int) -> InlineKeyboardMarkup:
    """Handle topup reject reason kb.

    Args:
        topup_id: Value for topup_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Неверная сумма",
                    callback_data=f"topup_reason:amount:{topup_id}",
                ),
                InlineKeyboardButton(
                    text="Чек некорректен",
                    callback_data=f"topup_reason:receipt:{topup_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Недостаточно данных",
                    callback_data=f"topup_reason:data:{topup_id}",
                ),
                InlineKeyboardButton(
                    text="Другая причина",
                    callback_data=f"topup_reason:other:{topup_id}",
                ),
            ],
        ]
    )


def topup_confirm_kb() -> InlineKeyboardMarkup:
    """Handle topup confirm kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, подтверждаю", callback_data="topup_confirm:yes"
                ),
                InlineKeyboardButton(
                    text="Нет, отменить", callback_data="topup_confirm:no"
                ),
            ]
        ]
    )


def roulette_result_kb() -> InlineKeyboardMarkup:
    """Handle roulette result kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎰 Крутить еще раз",
                    callback_data="roulette:start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Меню",
                    callback_data="services:menu",
                )
            ],
        ]
    )
