"""Module for vip functionality."""

from __future__ import annotations

from datetime import datetime


def is_vip_until(vip_until: datetime | None) -> bool:
    """Check whether vip until.

    Args:
        vip_until: Value for vip_until.

    Returns:
        Return value.
    """
    if not vip_until:
        return False
    return vip_until >= datetime.utcnow()


def free_fee_active(free_fee_until: datetime | None) -> bool:
    """Handle free fee active.

    Args:
        free_fee_until: Value for free_fee_until.

    Returns:
        Return value.
    """
    if not free_fee_until:
        return False
    return free_fee_until >= datetime.utcnow()
