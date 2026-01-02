"""Module for scammers functionality."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import or_, select

from bot.db.models import Scammer


def _norm_username(value: str | None) -> str | None:
    """Handle norm username.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    if not value:
        return None
    value = value.strip()
    if value.startswith("@"):
        value = value[1:]
    return value.lower() if value else None


def _norm_account(value: str | None) -> str | None:
    """Handle norm account.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    if not value:
        return None
    return value.strip()


async def find_scammer(
    session,
    *,
    user_id: int | None = None,
    username: str | None = None,
    account_id: str | None = None,
) -> Optional[Scammer]:
    """Handle find scammer.

    Args:
        session: Value for session.
        user_id: Value for user_id.
        username: Value for username.
        account_id: Value for account_id.

    Returns:
        Return value.
    """
    username = _norm_username(username)
    account_id = _norm_account(account_id)
    conditions = []
    if user_id:
        conditions.append(Scammer.user_id == user_id)
    if username:
        conditions.append(Scammer.username == username)
    if account_id:
        conditions.append(Scammer.account_id == account_id)
    if not conditions:
        return None
    result = await session.execute(select(Scammer).where(or_(*conditions)))
    return result.scalar_one_or_none()
