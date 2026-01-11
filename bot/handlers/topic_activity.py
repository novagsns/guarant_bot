# -*- coding: utf-8 -*-
"""Track activity in a specific topic for leaderboard rewards."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.services.topic_activity import (
    TARGET_CHAT_ID,
    TARGET_TOPIC_ID,
    record_topic_message,
)

router = Router()


@router.message(F.chat.id == TARGET_CHAT_ID)
async def topic_activity_tracker(
    message: Message, sessionmaker: async_sessionmaker
) -> None:
    if message.message_thread_id != TARGET_TOPIC_ID:
        return
    if not message.from_user or message.from_user.is_bot:
        return
    if message.text and message.text.strip().startswith("/"):
        return

    await record_topic_message(
        sessionmaker,
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
