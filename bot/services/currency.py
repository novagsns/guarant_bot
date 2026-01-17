"""Currency conversion helpers bound to USDT."""

from __future__ import annotations

from decimal import Decimal

from bot.config import Settings

_USDT_QUANT = Decimal("0.0001")
_COIN_QUANT = Decimal("0.01")
_RATE_QUANT = Decimal("0.0001")


def usdt_per_rub_rate(settings: Settings) -> Decimal:
    """Return USDT per 1 RUB."""
    return (Decimal("1") / settings.usdt_rate_rub).quantize(_USDT_QUANT)


def coins_per_rub_rate(settings: Settings) -> Decimal:
    """Return GSNS Coins per 1 RUB derived from USDT."""
    return (settings.coins_per_usdt / settings.usdt_rate_rub).quantize(_RATE_QUANT)


def rub_to_usdt(amount_rub: Decimal, settings: Settings) -> Decimal:
    """Convert RUB to USDT with fixed precision."""
    return (amount_rub / settings.usdt_rate_rub).quantize(_USDT_QUANT)


def rub_to_coins(amount_rub: Decimal, settings: Settings) -> Decimal:
    """Convert RUB to GSNS Coins using the USDT peg."""
    return (amount_rub / settings.usdt_rate_rub * settings.coins_per_usdt).quantize(
        _COIN_QUANT
    )


def usdt_to_coins(amount_usdt: Decimal, settings: Settings) -> Decimal:
    """Convert USDT to GSNS Coins."""
    return (amount_usdt * settings.coins_per_usdt).quantize(_COIN_QUANT)
