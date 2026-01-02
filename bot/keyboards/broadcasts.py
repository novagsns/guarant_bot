"""Module for broadcasts functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def broadcast_request_kb(request_id: int) -> InlineKeyboardMarkup:
    """Handle broadcast request kb.

    Args:
        request_id: Value for request_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Одобрить", callback_data=f"broadcast_approve:{request_id}"
                ),
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"broadcast_reject:{request_id}"
                ),
            ]
        ]
    )
