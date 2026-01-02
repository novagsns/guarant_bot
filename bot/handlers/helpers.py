"""Module for helpers functionality."""

from __future__ import annotations

from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User


async def get_or_create_user(session: AsyncSession, tg_user: TgUser) -> User:
    """Get or create user.

    Args:
        session: Value for session.
        tg_user: Value for tg_user.

    Returns:
        Return value.
    """
    result = await session.execute(select(User).where(User.id == tg_user.id))
    user = result.scalar_one_or_none()
    if user:
        if user.username != tg_user.username or user.full_name != tg_user.full_name:
            user.username = tg_user.username
            user.full_name = tg_user.full_name
            await session.commit()
        return user

    user = User(
        id=tg_user.id,
        username=tg_user.username,
        full_name=tg_user.full_name,
    )
    session.add(user)
    await session.commit()
    return user
