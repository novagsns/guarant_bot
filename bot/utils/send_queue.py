"""Module for queued send functionality."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)


@dataclass
class SendTask:
    """Represent send task."""

    args: tuple
    kwargs: dict
    future: asyncio.Future


class SendQueue:
    """Queue outgoing send_message calls with throttling and retries."""

    def __init__(
        self,
        *,
        delay_seconds: float,
        pause_every: int,
        pause_seconds: float,
        max_retries: int,
    ) -> None:
        self._delay_seconds = max(delay_seconds, 0.0)
        self._pause_every = max(pause_every, 0)
        self._pause_seconds = max(pause_seconds, 0.0)
        self._max_retries = max(max_retries, 0)
        self._queue: asyncio.Queue[SendTask] = asyncio.Queue()
        self._sent_count = 0

    async def enqueue(self, args: tuple, kwargs: dict) -> object:
        """Enqueue a send_message call."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put(SendTask(args=args, kwargs=kwargs, future=future))
        return await future

    async def run(self, bot) -> None:
        """Run the send queue worker."""
        raw_send = getattr(bot, "_raw_send_message", bot.send_message)
        while True:
            task = await self._queue.get()
            retries = 0
            while True:
                try:
                    result = await raw_send(*task.args, **task.kwargs)
                    task.future.set_result(result)
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after + 0.2)
                    retries += 1
                except TelegramNetworkError as exc:
                    retries += 1
                    if retries > self._max_retries:
                        task.future.set_exception(exc)
                        break
                    await asyncio.sleep(1 + retries)
                except (TelegramForbiddenError, TelegramBadRequest) as exc:
                    task.future.set_exception(exc)
                    break
                except Exception as exc:
                    task.future.set_exception(exc)
                    break

            self._sent_count += 1
            if self._pause_every and self._sent_count % self._pause_every == 0:
                await asyncio.sleep(self._pause_seconds)
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
