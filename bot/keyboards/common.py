"""Module for common functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

REVIEW_MENU_BUTTON = "📝 Просмотреть отзывы"


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
            [KeyboardButton(text=REVIEW_MENU_BUTTON)],
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


def tools_menu_kb() -> InlineKeyboardMarkup:
    """Handle tools menu kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001f50e \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f",
                    callback_data="scammers_public:check",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f9fe \u0411\u0430\u0437\u0430 \u0441\u043a\u0430\u043c\u0435\u0440\u043e\u0432",
                    callback_data="info:scammers",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f194 \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u0430\u043a\u043a\u0430\u0443\u043d\u0442",
                    callback_data="tools:account_check",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4b0 \u041a\u0430\u043b\u044c\u043a\u0443\u043b\u044f\u0442\u043e\u0440 \u043a\u043e\u043c\u0438\u0441\u0441\u0438\u0438",
                    callback_data="tools:fee",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4cc \u041c\u043e\u0438 \u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u044f",
                    callback_data="tools:restrictions",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434",
                    callback_data="tools:back",
                )
            ],
        ]
    )


def tools_fee_type_kb() -> InlineKeyboardMarkup:
    """Handle tools fee type kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u041f\u0440\u043e\u0434\u0430\u0436\u0430",
                    callback_data="tools:fee_type:sale",
                ),
                InlineKeyboardButton(
                    text="\u041e\u0431\u043c\u0435\u043d",
                    callback_data="tools:fee_type:exchange",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\u041e\u0431\u043c\u0435\u043d \u0441 \u0434\u043e\u043f\u043b\u0430\u0442\u043e\u0439",
                    callback_data="tools:fee_type:exchange_with_addon",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\u0420\u0430\u0441\u0441\u0440\u043e\u0447\u043a\u0430",
                    callback_data="tools:fee_type:installment",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434",
                    callback_data="tools:back",
                )
            ],
        ]
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
