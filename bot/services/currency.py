"""Currency conversion helpers bound to USDT."""

from __future__ import annotations

from decimal import Decimal

from bot.config import Settings
from bot.services.market_rates import get_cached_usdt_rate_rub

_USDT_QUANT = Decimal("0.0001")
_COIN_QUANT = Decimal("0.01")
_RATE_QUANT = Decimal("0.0001")


def _resolve_usdt_rate(settings: Settings) -> Decimal:
    cached = get_cached_usdt_rate_rub()
    if cached and cached > 0:
        return cached
    return settings.usdt_rate_rub


def usdt_per_rub_rate(settings: Settings) -> Decimal:
    """Return USDT per 1 RUB."""
    rate = _resolve_usdt_rate(settings)
    return (Decimal("1") / rate).quantize(_USDT_QUANT)


def coins_per_rub_rate(settings: Settings) -> Decimal:
    """Return GSNS Coins per 1 RUB derived from USDT."""
    rate = _resolve_usdt_rate(settings)
    return (settings.coins_per_usdt / rate).quantize(_RATE_QUANT)


def rub_to_usdt(amount_rub: Decimal, settings: Settings) -> Decimal:
    """Convert RUB to USDT with fixed precision."""
    rate = _resolve_usdt_rate(settings)
    return (amount_rub / rate).quantize(_USDT_QUANT)


def rub_to_coins(amount_rub: Decimal, settings: Settings) -> Decimal:
    """Convert RUB to GSNS Coins using the USDT peg."""
    rate = _resolve_usdt_rate(settings)
    return (amount_rub / rate * settings.coins_per_usdt).quantize(_COIN_QUANT)


def usdt_to_coins(amount_usdt: Decimal, settings: Settings) -> Decimal:
    """Convert USDT to GSNS Coins."""
    return (amount_usdt * settings.coins_per_usdt).quantize(_COIN_QUANT)
