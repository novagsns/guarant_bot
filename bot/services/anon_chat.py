"""Module for anon chat functionality."""

from __future__ import annotations


def role_label(role: str) -> str:
    """Handle role label.

    Args:
        role: Value for role.

    Returns:
        Return value.
    """
    if role == "buyer":
        return "Покупатель"
    if role == "seller":
        return "Продавец"
    if role == "guarantor":
        return "Гарант"
    return "Пользователь"
