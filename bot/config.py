"""Module for config functionality."""

from __future__ import annotations

import os
from pathlib import Path
from decimal import Decimal
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv


def _parse_int_list(value: str | None) -> List[int]:
    """Handle parse int list.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    if not value:
        return []
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return [int(p) for p in parts]


def _parse_str_list(value: str | None) -> List[str]:
    """Handle parse str list.

    Args:
        value: Value for value.

    Returns:
        Return value.
    """
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    """Represent Settings.

    Attributes:
        bot_token: Attribute value.
        bot_username: Attribute value.
        admin_chat_id: Attribute value.
        admin_topic_id: Attribute value.
        owner_ids: Attribute value.
        database_url: Attribute value.
        default_games: Attribute value.
        wallet_trc20: Attribute value.
        referral_bonus: Attribute value.
        coins_per_rub: Attribute value.
        usdt_rate_rub: Attribute value.
        min_topup_rub: Attribute value.
        moderation_blacklist: Attribute value.
    """

    bot_token: str
    bot_username: str
    admin_chat_id: int
    admin_topic_id: int | None
    owner_ids: List[int]
    database_url: str
    default_games: List[str]
    wallet_trc20: str
    referral_bonus: int
    coins_per_rub: Decimal
    usdt_rate_rub: Decimal
    min_topup_rub: Decimal
    moderation_blacklist: List[str]


def load_settings() -> Settings:
    """Load settings.

    Returns:
        Return value.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    bot_username = os.getenv("BOT_USERNAME", "").strip()
    admin_chat_id = int(os.getenv("ADMIN_CHAT_ID", "0"))
    admin_topic_id_raw = os.getenv("ADMIN_TOPIC_ID", "").strip()
    admin_topic_id = int(admin_topic_id_raw) if admin_topic_id_raw else None

    owner_ids = _parse_int_list(os.getenv("OWNER_IDS"))
    database_url = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db"
    ).strip()
    default_games = _parse_str_list(
        os.getenv("DEFAULT_GAMES", "MLBB,Tanks,PUBG,Genshin")
    )
    wallet_trc20 = os.getenv("WALLET_TRC20", "").strip()
    referral_bonus = int(os.getenv("REFERRAL_BONUS", "10"))
    coins_per_rub = Decimal(os.getenv("COINS_PER_RUB", "10"))
    usdt_rate_rub = Decimal(os.getenv("USDT_RATE_RUB", "100"))
    min_topup_rub = Decimal(os.getenv("MIN_TOPUP_RUB", "500"))
    moderation_blacklist = _parse_str_list(
        os.getenv(
            "MODERATION_BLACKLIST",
            "casino,казино,научу зарабатывать,обучаю зарабатывать,легкий заработок,"
            "быстрый заработок,инвестиции,удаленная работа,работа дома",
        )
    )

    return Settings(
        bot_token=bot_token,
        bot_username=bot_username,
        admin_chat_id=admin_chat_id,
        admin_topic_id=admin_topic_id,
        owner_ids=owner_ids,
        database_url=database_url,
        default_games=default_games,
        wallet_trc20=wallet_trc20,
        referral_bonus=referral_bonus,
        coins_per_rub=coins_per_rub,
        usdt_rate_rub=usdt_rate_rub,
        min_topup_rub=min_topup_rub,
        moderation_blacklist=moderation_blacklist,
    )
