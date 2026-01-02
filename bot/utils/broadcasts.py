"""Module for broadcasts functionality."""

from __future__ import annotations

from aiogram import Bot
from bot.config import Settings
from bot.db.models import BroadcastRequest
from bot.keyboards.broadcasts import broadcast_request_kb
from bot.utils.admin_target import get_admin_target


async def create_broadcast_request(
    session,
    bot: Bot,
    settings: Settings,
    *,
    creator_id: int,
    text: str,
    kind: str,
    cost: float = 0,
    ad_id: int | None = None,
) -> BroadcastRequest:
    """Create broadcast request.

    Args:
        session: Value for session.
        bot: Value for bot.
        settings: Value for settings.
        creator_id: Value for creator_id.
        text: Value for text.
        kind: Value for kind.
        cost: Value for cost.
        ad_id: Value for ad_id.

    Returns:
        Return value.
    """
    request = BroadcastRequest(
        creator_id=creator_id,
        ad_id=ad_id,
        kind=kind,
        text=text,
        cost=cost,
    )
    session.add(request)
    await session.commit()

    chat_id, topic_id = get_admin_target(settings)
    if chat_id != 0:
        await bot.send_message(
            chat_id,
            (
                "Запрос рассылки\n"
                f"ID: {request.id}\n"
                f"Тип: {kind}\n"
                f"От: {creator_id}\n"
                f"Стоимость: {cost} Coins\n\n"
                f"{text}"
            ),
            message_thread_id=topic_id,
            reply_markup=broadcast_request_kb(request.id),
        )
    return request
