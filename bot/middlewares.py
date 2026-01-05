"""Module for middlewares functionality."""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Awaitable, Callable, Dict

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.db.models import User, UserAction
from bot.keyboards.info import support_only_kb
from bot.utils.scammers import find_scammer
from bot.services.trust import apply_trust_event
from bot.services.trust import apply_deal_no_dispute_bonus, apply_monthly_activity_bonus


class ContextMiddleware(BaseMiddleware):
    """Represent ContextMiddleware."""

    def __init__(self, sessionmaker, settings) -> None:
        """Handle init.

        Args:
            sessionmaker: Value for sessionmaker.
            settings: Value for settings.
        """
        super().__init__()
        self._sessionmaker = sessionmaker
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        """Handle call.

        Args:
            handler: Value for handler.
            event: Value for event.
            data: Value for data.

        Returns:
            Return value.
        """
        data["sessionmaker"] = self._sessionmaker
        data["settings"] = self._settings
        return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Represent AccessMiddleware."""

    def __init__(self, sessionmaker, settings) -> None:
        """Handle init.

        Args:
            sessionmaker: Value for sessionmaker.
            settings: Value for settings.
        """
        super().__init__()
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._last_warning: dict[int, datetime] = {}

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        """Handle call.

        Args:
            handler: Value for handler.
            event: Value for event.
            data: Value for data.

        Returns:
            Return value.
        """
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        if isinstance(event, Message):
            state = data.get("state")
            if state and event.text:
                normalized = re.sub(
                    r"^[^\w\u0400-\u04FF0-9]+\s*", "", event.text
                ).strip()
                menu_texts = {
                    "Сделки и объявления",
                    "Инструменты",
                    "Услуги сети",
                    "Профиль",
                    "Информация",
                    "Управление персоналом",
                    "Назад",
                    "Все объявления",
                    "Продать аккаунт",
                    "Мои объявления",
                    "Обмен",
                    "Предложить обмен",
                    "Все обмены",
                }
                if normalized in menu_texts:
                    await state.clear()

        async with self._sessionmaker() as session:
            result = await session.execute(select(User).where(User.id == user.id))
            db_user = result.scalar_one_or_none()
            # Only scammers are blocked from using the bot; chat bans do not apply here.
            scammer = await find_scammer(
                session, user_id=user.id, username=user.username
            )
            if not scammer:
                return await handler(event, data)

            result = await session.execute(select(User).where(User.id == user.id))
            db_user = result.scalar_one_or_none()
            if db_user and db_user.role != "banned":
                db_user.role = "banned"
                await apply_trust_event(
                    session,
                    user.id,
                    "banned_link",
                    -20,
                    "Связь с баном",
                    ref_type="ban",
                    ref_id=user.id,
                )
                await session.commit()

        return await self._block_if_not_support(event, data, handler)


    async def _block_if_not_support(self, event, data, handler):
        if isinstance(event, CallbackQuery):
            if event.data == "support:start":
                return await handler(event, data)
            await event.message.answer(
                "Ваш доступ ограничен. Доступна только поддержка.",
                reply_markup=support_only_kb(),
            )
            await event.answer()
            return None

        if isinstance(event, Message):
            state = data.get("state")
            if state:
                try:
                    current = await state.get_state()
                    if current and current.endswith("SupportStates:active"):
                        return await handler(event, data)
                except Exception:
                    pass
            if event.text and event.text.strip().startswith("/support"):
                return await handler(event, data)

            now = datetime.utcnow()
            last = self._last_warning.get(event.from_user.id)
            if not last or now - last >= timedelta(minutes=1):
                await event.answer(
                    "Ваш доступ ограничен. Доступна только поддержка.",
                    reply_markup=support_only_kb(),
                )
                self._last_warning[event.from_user.id] = now
            return None

        return await handler(event, data)


class ActionLogMiddleware(BaseMiddleware):
    """Represent ActionLogMiddleware."""

    def __init__(self, sessionmaker) -> None:
        """Handle init.

        Args:
            sessionmaker: Value for sessionmaker.
        """
        super().__init__()
        self._sessionmaker = sessionmaker

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        """Handle call.

        Args:
            handler: Value for handler.
            event: Value for event.
            data: Value for data.

        Returns:
            Return value.
        """
        user = getattr(event, "from_user", None)
        if user and isinstance(event, (Message, CallbackQuery)):
            if isinstance(event, Message) and event.chat.type != "private":
                return await handler(event, data)
            action_type = "callback" if isinstance(event, CallbackQuery) else "message"
            if isinstance(event, CallbackQuery):
                action = event.data or ""
            else:
                action = event.text or f"[{event.content_type}]"
            action = action.strip()
            if action:
                if len(action) > 255:
                    action = action[:255]
                async with self._sessionmaker() as session:
                    result = await session.execute(
                        select(User).where(User.id == user.id)
                    )
                    db_user = result.scalar_one_or_none()
                    if not db_user:
                        session.add(
                            User(
                                id=user.id,
                                username=user.username,
                                full_name=user.full_name,
                            )
                        )
                    session.add(
                        UserAction(
                            user_id=user.id,
                            action_type=action_type,
                            action=action,
                        )
                    )
                    await session.commit()
                    await apply_monthly_activity_bonus(session, user.id)
                    await apply_deal_no_dispute_bonus(session, user.id)
        return await handler(event, data)
