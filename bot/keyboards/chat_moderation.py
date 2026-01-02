"""Module for chat moderation functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def chat_moderation_kb(
    chats: list[tuple[int, str | None, bool]],
) -> InlineKeyboardMarkup:
    """Handle chat moderation kb.

    Args:
        chats: Value for chats.

    Returns:
        Return value.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, title, active in chats:
        label = title or f"ID {chat_id}"
        status = "🟢" if active else "🔴"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {label}",
                    callback_data=f"chat_mod_toggle:{chat_id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="➕ Добавить чат", callback_data="chat_mod_add")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить", callback_data="owner:chat_moderation"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
