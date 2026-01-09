"""Module for fees functionality."""

from __future__ import annotations

from decimal import Decimal

from bot.services.trade_bonus import DEAL_MIN_AMOUNT, TradeLevel

EXCHANGE_CONFIG: dict[int, tuple[Decimal, Decimal]] = {
    0: (Decimal("400"), Decimal("0.10")),
    1: (Decimal("370"), Decimal("0.09")),
    2: (Decimal("340"), Decimal("0.09")),
    3: (Decimal("300"), Decimal("0.07")),
}

INSTALLMENT_RATES: dict[int, Decimal] = {
    0: Decimal("0.12"),
    1: Decimal("0.12"),
    2: Decimal("0.11"),
    3: Decimal("0.10"),
}


def _to_decimal(value: float | int | str) -> Decimal:
    """Convert a number to Decimal."""

    return Decimal(str(value))


def calculate_fee(
    amount: float | int | str | None,
    deal_type: str,
    addon_amount: float | int | str | None = None,
    trust_score: int | None = None,
    *,
    trade_level: TradeLevel | None = None,
    vip: bool = False,
) -> Decimal | None:
    """Calculate deal fee based on the type and bonuses."""

    if amount is None:
        return None

    amount_dec = _to_decimal(amount)
    level = trade_level.tier if trade_level else 0
    vip_sale = vip and amount_dec >= DEAL_MIN_AMOUNT
    vip_installment = vip and amount_dec >= DEAL_MIN_AMOUNT

    if deal_type == "exchange":
        idx = level if vip else 0
        base_fee, _ = EXCHANGE_CONFIG.get(idx, EXCHANGE_CONFIG[0])
        return base_fee
    if deal_type == "exchange_with_addon":
        addon_dec = _to_decimal(addon_amount or 0)
        idx = level if vip else 0
        base_fee, addon_rate = EXCHANGE_CONFIG.get(idx, EXCHANGE_CONFIG[0])
        return base_fee + addon_dec * addon_rate
    if deal_type == "installment":
        rate_key = level if vip_installment else 0
        rate = INSTALLMENT_RATES.get(rate_key, INSTALLMENT_RATES[0])
        rate = max(rate - _trust_discount(trust_score), Decimal("0"))
        return amount_dec * rate
    if deal_type in {"contact", "chat"}:
        return Decimal("0")

    return _calculate_buy_fee(amount_dec, trust_score, level, vip_sale)


def _calculate_buy_fee(
    amount: Decimal, trust_score: int | None, level: int, vip: bool
) -> Decimal:
    """Calculate commission for buy/sale deals."""

    if amount < Decimal("2000"):
        return Decimal("200")

    if amount >= Decimal("25000"):
        base_rate = Decimal("0.08")
    else:
        base_rate = Decimal("0.10")

    vip_discount = Decimal(level) * Decimal("0.01") if vip else Decimal("0")
    rate = max(base_rate - vip_discount, Decimal("0.06"))
    rate = max(rate - _trust_discount(trust_score), Decimal("0.06"))
    return amount * rate


def _trust_discount(trust_score: int | None) -> Decimal:
    """Return the trust-based discount."""

    if trust_score is None:
        return Decimal("0")
    if trust_score >= 70:
        return Decimal("0.07")
    if trust_score >= 40:
        return Decimal("0.04")
    if trust_score >= 20:
        return Decimal("0.02")
    return Decimal("0")
