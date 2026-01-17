"""Market rate cache for USDT/RUB."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal, InvalidOperation

import aiohttp

_RATE_LOCK = asyncio.Lock()
_LAST_RATE: Decimal | None = None
_LAST_UPDATED = 0.0
_DEFAULT_INTERVAL = 300
_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def _fetch_coingecko() -> Decimal | None:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub"
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json()
    value = data.get("tether", {}).get("rub")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


async def _fetch_binance() -> Decimal | None:
    url = "https://api.binance.com/api/v3/ticker/price?symbol=USDTRUB"
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json()
    value = data.get("price")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


async def refresh_usdt_rate_rub() -> Decimal | None:
    """Fetch and cache the current USDT/RUB rate."""
    global _LAST_RATE, _LAST_UPDATED
    for fetcher in (_fetch_coingecko, _fetch_binance):
        try:
            rate = await fetcher()
        except Exception:
            rate = None
        if rate and rate > 0:
            async with _RATE_LOCK:
                _LAST_RATE = rate
                _LAST_UPDATED = time.monotonic()
            return rate
    return None


def get_cached_usdt_rate_rub() -> Decimal | None:
    """Return cached USDT/RUB rate if available."""
    return _LAST_RATE


async def usdt_rate_loop(interval_seconds: int = _DEFAULT_INTERVAL) -> None:
    """Periodically refresh the cached USDT/RUB rate."""
    while True:
        await refresh_usdt_rate_rub()
        await asyncio.sleep(interval_seconds)
