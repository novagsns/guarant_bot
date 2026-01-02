"""Module for profile functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def profile_actions_kb() -> InlineKeyboardMarkup:
    """Handle profile actions kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧾 Мои сделки", callback_data="profile:deals"
                ),
                InlineKeyboardButton(
                    text="🗂 Мои объявления", callback_data="profile:ads"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 Баланс и операции", callback_data="profile:wallet"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛒 Покупки услуг",
                    callback_data="profile:service_purchases",
                )
            ],
            [InlineKeyboardButton(text="💎 VIP функции", callback_data="profile:vip")],
        ]
    )


def wallet_tx_kb(tx_id: int) -> InlineKeyboardMarkup:
    """Handle wallet tx kb.

    Args:
        tx_id: Value for tx_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Подробнее", callback_data=f"wallet_tx:{tx_id}"
                )
            ]
        ]
    )


def deal_list_kb(deals: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Handle deal list kb.

    Args:
        deals: Value for deals.

    Returns:
        Return value.
    """
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"profile_deal:{deal_id}")]
        for deal_id, label in deals
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deal_detail_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Handle deal detail kb.

    Args:
        deal_id: Value for deal_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Экспорт .txt", callback_data=f"export_deal:{deal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Оставить отзыв",
                    callback_data=f"review_start:{deal_id}",
                )
            ],
        ]
    )


def my_ad_manage_kb(ad_id: int) -> InlineKeyboardMarkup:
    """Handle my ad manage kb.

    Args:
        ad_id: Value for ad_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать", callback_data=f"edit_ad:{ad_id}"
                ),
                InlineKeyboardButton(
                    text="🗑️ Удалить", callback_data=f"delete_ad:{ad_id}"
                ),
            ]
        ]
    )


def ad_edit_kb(ad_id: int) -> InlineKeyboardMarkup:
    """Handle ad edit kb.

    Args:
        ad_id: Value for ad_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Заголовок", callback_data=f"edit_field:title:{ad_id}"
                ),
                InlineKeyboardButton(
                    text="📄 Описание",
                    callback_data=f"edit_field:description:{ad_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💰 Цена", callback_data=f"edit_field:price:{ad_id}"
                ),
                InlineKeyboardButton(
                    text="💳 Оплата", callback_data=f"edit_field:payment:{ad_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🖼️ Медиа", callback_data=f"edit_field:media:{ad_id}"
                ),
                InlineKeyboardButton(
                    text="🎮 Игра", callback_data=f"edit_field:game:{ad_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👁 Показать/скрыть", callback_data=f"toggle_ad:{ad_id}"
                )
            ],
        ]
    )
