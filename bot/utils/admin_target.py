"""Module for admin target functionality."""

from __future__ import annotations

from typing import Tuple

from bot.config import Settings

_ADMIN_TARGET: dict | None = None


def set_admin_target(chat_id: int, topic_id: int | None) -> None:
    """Set admin target.

    Args:
        chat_id: Value for chat_id.
        topic_id: Value for topic_id.
    """
    global _ADMIN_TARGET
    _ADMIN_TARGET = {"chat_id": chat_id, "topic_id": topic_id}


def clear_admin_target() -> None:
    """Handle clear admin target."""
    global _ADMIN_TARGET
    _ADMIN_TARGET = None


def get_admin_target(settings: Settings) -> Tuple[int, int | None]:
    """Get admin target.

    Args:
        settings: Value for settings.

    Returns:
        Return value.
    """
    if _ADMIN_TARGET:
        chat_id = _ADMIN_TARGET.get("chat_id")
        topic_id = _ADMIN_TARGET.get("topic_id")
        if isinstance(chat_id, int):
            return chat_id, topic_id if isinstance(topic_id, int) else None
    topic_id = settings.admin_topic_id if settings.admin_topic_id else None
    return settings.admin_chat_id, topic_id
