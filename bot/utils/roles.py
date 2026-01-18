"""Module for roles functionality."""

from __future__ import annotations

from typing import Iterable

STAFF_ROLES = {"owner", "admin", "moderator", "designer", "guarantor"}


def is_owner(role: str, owner_ids: Iterable[int], user_id: int) -> bool:
    """Check whether owner.

    Args:
        role: Value for role.
        owner_ids: Value for owner_ids.
        user_id: Value for user_id.

    Returns:
        Return value.
    """
    return role == "owner" or user_id in owner_ids


def is_staff(role: str) -> bool:
    """Check whether staff.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    return role in STAFF_ROLES


def role_label(role: str) -> str:
    """Handle role label.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    mapping = {
        "owner": "Владелец",
        "admin": "Главный админ",
        "moderator": "Модератор",
        "designer": "Дизайнер",
        "guarantor": "Гарант",
        "banned": "Забанен",
        "user": "Пользователь",
    }
    return mapping.get(role, role)
