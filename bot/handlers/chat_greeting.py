"""Greeting handler for group chats."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import ChatMemberUpdated

from bot.keyboards.common import referral_kb
from bot.utils.texts import CHAT_WELCOME_TEXT

router = Router()


@router.chat_member()
async def welcome_new_member(event: ChatMemberUpdated) -> None:
    """Send a simple welcome message when a user joins a group."""
    user = getattr(event.new_chat_member, "user", None)
    if not user or user.is_bot:
        return
    if event.chat.type not in {"group", "supergroup"}:
        return
    if event.new_chat_member.status != "member":
        return
    if event.old_chat_member.status not in {"left", "kicked"}:
        return

    text = CHAT_WELCOME_TEXT.format(name=user.full_name)
    try:
        await event.bot.send_message(event.chat.id, text, reply_markup=referral_kb())
    except Exception:
        pass
