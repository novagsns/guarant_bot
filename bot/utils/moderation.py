"""Module for moderation functionality."""

from __future__ import annotations

import re

_PATTERNS = [
    r"(https?://|www\.|t\.me/|telegram\.me/|telegram\.org|tg://)",
    r"@\w+",
    r"(discord\.gg|vk\.com|vk\.cc|wa\.me)",
]


def contains_prohibited(text: str | None) -> bool:
    """Handle contains prohibited.

    Args:
        text: Value for text.

    Returns:
        Return value.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in _PATTERNS)


def contains_blacklist(text: str | None, words: list[str]) -> bool:
    """Handle contains blacklist.

    Args:
        text: Value for text.
        words: Value for words.

    Returns:
        Return value.
    """
    if not text:
        return False
    lowered = text.lower()
    for word in words:
        token = word.strip().lower()
        if token and token in lowered:
            return True
    return False
