"""Module for vip functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def vip_menu_kb() -> InlineKeyboardMarkup:
    """Handle vip menu kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Платная рассылка (3000 Coins)",
                    callback_data="vip:broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Бесплатная сделка на 7 дней (6000 Coins)",
                    callback_data="vip:free_deal",
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data="profile:back")],
        ]
    )
