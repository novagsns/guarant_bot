"""Module for fees functionality."""

from __future__ import annotations

from decimal import Decimal


def _to_decimal(value: float | int | str) -> Decimal:
    """Handle to decimal.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    return Decimal(str(value))


def calculate_fee(
    amount: float | int | str | None,
    deal_type: str,
    addon_amount: float | int | str | None = None,
    trust_score: int | None = None,
) -> Decimal | None:
    """Handle calculate fee.

    Args:
        amount: Value for amount.
        deal_type: Value for deal_type.
        addon_amount: Value for addon_amount.
        trust_score: Value for trust_score.

    Returns:
        Return value.
    """
    if amount is None:
        return None

    amount_dec = _to_decimal(amount)

    if deal_type == "exchange":
        return Decimal("400")
    if deal_type == "exchange_with_addon":
        addon = _to_decimal(addon_amount or 0)
        return Decimal("400") + addon * Decimal("0.10")
    if deal_type == "installment":
        rate = Decimal("0.14") - _trust_discount(trust_score)
        if rate < Decimal("0"):
            rate = Decimal("0")
        return amount_dec * rate
    if deal_type in {"contact", "chat"}:
        return Decimal("0")

    if amount_dec < Decimal("2000"):
        return Decimal("250")
    if amount_dec < Decimal("25000"):
        rate = Decimal("0.12") - _trust_discount(trust_score)
        if rate < Decimal("0"):
            rate = Decimal("0")
        return amount_dec * rate
    rate = Decimal("0.10") - _trust_discount(trust_score)
    if rate < Decimal("0"):
        rate = Decimal("0")
    return amount_dec * rate


def _trust_discount(trust_score: int | None) -> Decimal:
    """Handle trust discount.

    Args:
        trust_score: Value for trust_score.

    Returns:
        Return value.
    """
    if trust_score is None:
        return Decimal("0")
    if trust_score >= 70:
        return Decimal("0.07")
    if trust_score >= 40:
        return Decimal("0.04")
    if trust_score >= 20:
        return Decimal("0.02")
    return Decimal("0")
