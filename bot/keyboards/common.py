"""Module for common functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Handle main menu kb.

    Returns:
        Return value.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📦 Сделки и объявления"),
                KeyboardButton(text="🧰 Инструменты"),
            ],
            [
                KeyboardButton(text="🛒 Услуги сети"),
                KeyboardButton(text="👤 Профиль"),
            ],
            [KeyboardButton(text="ℹ️ Информация")],
            [KeyboardButton(text="👑 Управление персоналом")],
        ],
        resize_keyboard=True,
    )


def deals_menu_kb() -> ReplyKeyboardMarkup:
    """Handle deals menu kb.

    Returns:
        Return value.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗂 Все объявления")],
            [KeyboardButton(text="🛒 Продать аккаунт")],
            [KeyboardButton(text="🗂 Мои объявления")],
            [KeyboardButton(text="🔁 Обмен")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def exchange_menu_kb() -> ReplyKeyboardMarkup:
    """Handle exchange menu kb.

    Returns:
        Return value.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Предложить обмен")],
            [KeyboardButton(text="🗂 Все обмены")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def referral_kb() -> InlineKeyboardMarkup:
    """Handle referral kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Выгодный донат для вашей игры",
                    url="https://donatov.net/ref/GSNS_MLBB",
                )
            ]
        ]
    )
