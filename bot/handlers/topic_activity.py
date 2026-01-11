# -*- coding: utf-8 -*-
"""Track activity in a specific topic for leaderboard rewards."""

from __future__ import annotations

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.services.topic_activity import (
    TARGET_CHAT_ID,
    TARGET_TOPIC_ID,
    maybe_update_leaderboard,
    record_topic_message,
)

router = Router()


@router.message()
async def topic_activity_tracker(
    message: Message, sessionmaker: async_sessionmaker
) -> None:
    if message.chat.id != TARGET_CHAT_ID:
        raise SkipHandler
    if message.message_thread_id != TARGET_TOPIC_ID:
        raise SkipHandler
    if not message.from_user or message.from_user.is_bot:
        raise SkipHandler
    if message.text and message.text.strip().startswith("/"):
        raise SkipHandler

    counted = await record_topic_message(
        sessionmaker,
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    if counted:
        await maybe_update_leaderboard(message.bot, sessionmaker)
    raise SkipHandler
