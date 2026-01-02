"""Module for staff functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db.models import Game, User
from bot.handlers.helpers import get_or_create_user

router = Router()


def _can_manage_staff(user: User, settings: Settings) -> bool:
    """Handle can manage staff.

    Args:
        user: Value for user.
        settings: Value for settings.

    Returns:
        Return value.
    """
    if user.id in settings.owner_ids:
        return True
    return user.role in {"owner", "admin"}


@router.message(F.text.startswith("/add_game"))
async def add_game(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle add game.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_owner(user, settings):
            await message.answer("Нет прав.")
            return

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /add_game Название")
            return
        name = parts[1].strip()

        exists = await session.execute(select(Game).where(Game.name == name))
        if exists.scalar_one_or_none():
            await message.answer("Игра уже существует.")
            return

        session.add(Game(name=name, active=True))
        await session.commit()
    await message.answer(f"Игра добавлена: {name}")


@router.message(F.text.startswith("/remove_game"))
async def remove_game(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Handle remove game.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        if not _is_owner(user, settings):
            await message.answer("Нет прав.")
            return

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /remove_game Название")
            return
        name = parts[1].strip()

        result = await session.execute(select(Game).where(Game.name == name))
        game = result.scalar_one_or_none()
        if not game:
            await message.answer("Игра не найдена.")
            return

        game.active = False
        await session.commit()
    await message.answer(f"Игра деактивирована: {name}")


@router.message(F.text.startswith("/list_games"))
async def list_games(message: Message, sessionmaker: async_sessionmaker) -> None:
    """Handle list games.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(Game).order_by(Game.id))
        games = result.scalars().all()

    if not games:
        await message.answer("Список игр пуст.")
        return

    lines = ["Список игр:"]
    for game in games:
        status = "активна" if game.active else "скрыта"
        lines.append(f"- {game.name} ({status})")
    await message.answer("\n".join(lines))


@router.message(F.text.startswith("/set_role"))
async def set_role(
    message: Message, sessionmaker: async_sessionmaker, settings: Settings
) -> None:
    """Set role.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    async with sessionmaker() as session:
        owner = await get_or_create_user(session, message.from_user)
        if not _can_manage_staff(owner, settings):
            await message.answer("Нет прав.")
            return

        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("Использование: /set_role user_id role")
            return

        user_id = int(parts[1])
        role = parts[2].strip().lower()
        if role not in {"admin", "moderator", "designer", "guarantor"}:
            await message.answer("Неизвестная роль.")
            return

        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(id=user_id, role=role)
            session.add(user)
        else:
            user.role = role
        await session.commit()

    await message.answer(f"Роль обновлена: {user_id} -> {role}")
