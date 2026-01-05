"""Module for vip functionality."""

from __future__ import annotations

from datetime import datetime, timezone


def _is_active_until(until: datetime | None) -> bool:
    if not until:
        return False
    if until.tzinfo is None or until.tzinfo.utcoffset(until) is None:
        return until >= datetime.utcnow()
    return until >= datetime.now(timezone.utc)


def is_vip_until(vip_until: datetime | None) -> bool:
    """Check whether vip until.

    Args:
        vip_until: Value for vip_until.

    Returns:
        Return value.
    """
    return _is_active_until(vip_until)


def free_fee_active(free_fee_until: datetime | None) -> bool:
    """Handle free fee active.

    Args:
        free_fee_until: Value for free_fee_until.

    Returns:
        Return value.
    """
    return _is_active_until(free_fee_until)
