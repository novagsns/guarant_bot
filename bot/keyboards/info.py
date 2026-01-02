"""Module for info functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def info_menu_kb() -> InlineKeyboardMarkup:
    """Handle info menu kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❓ FAQ", callback_data="info:faq"),
                InlineKeyboardButton(text="🔒 Политика", callback_data="info:privacy"),
            ],
            [
                InlineKeyboardButton(text="👥 Сотрудники", callback_data="info:staff"),
                InlineKeyboardButton(text="🛡 Гаранты", callback_data="info:guards"),
            ],
            [
                InlineKeyboardButton(
                    text="💬 Поддержка", callback_data="support:start"
                ),
                InlineKeyboardButton(
                    text="⚠️ База скамеров", callback_data="info:scammers"
                ),
            ],
        ]
    )


def faq_list_kb() -> InlineKeyboardMarkup:
    """Handle faq list kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Что такое гарант‑сделка?",
                    callback_data="faq:guarantee",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Как разместить объявление?",
                    callback_data="faq:create_ad",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Как пополнить GSNS Coins?",
                    callback_data="faq:topup",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Комиссии и правила",
                    callback_data="faq:fees",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="info:back")],
        ]
    )


def faq_back_kb() -> InlineKeyboardMarkup:
    """Handle faq back kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="info:faq")]
        ]
    )


def info_back_kb() -> InlineKeyboardMarkup:
    """Handle info back kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="info:back")]
        ]
    )


def support_only_kb() -> InlineKeyboardMarkup:
    """Handle support only kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Поддержка", callback_data="support:start")]
        ]
    )
