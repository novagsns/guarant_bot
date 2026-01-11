# -*- coding: utf-8 -*-
"""Ad alert subscriptions and notifications."""

from __future__ import annotations

from decimal import Decimal
import html

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import Ad, Game
from bot.keyboards.ads import ad_actions_kb

MAX_SUBSCRIPTIONS = 10


async def _ensure_tables(session) -> None:
    if session.info.get("ad_alerts_ready"):
        return
    dialect = session.bind.dialect.name
    if dialect == "sqlite":
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS ad_alert_subscriptions ("
                "id INTEGER PRIMARY KEY,"
                "user_id BIGINT NOT NULL,"
                "game_id INTEGER,"
                "price_min NUMERIC(12,2),"
                "price_max NUMERIC(12,2),"
                "server_query VARCHAR(64),"
                "active BOOLEAN DEFAULT 1,"
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
        )
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS ad_alert_deliveries ("
                "id INTEGER PRIMARY KEY,"
                "user_id BIGINT NOT NULL,"
                "ad_id INTEGER NOT NULL,"
                "delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                "UNIQUE(user_id, ad_id)"
                ")"
            )
        )
        await session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_ad_alert_subscriptions_user "
                "ON ad_alert_subscriptions (user_id)"
            )
        )
    else:
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS ad_alert_subscriptions ("
                "id SERIAL PRIMARY KEY,"
                "user_id BIGINT NOT NULL,"
                "game_id INTEGER,"
                "price_min NUMERIC(12,2),"
                "price_max NUMERIC(12,2),"
                "server_query VARCHAR(64),"
                "active BOOLEAN DEFAULT TRUE,"
                "created_at TIMESTAMPTZ DEFAULT now()"
                ")"
            )
        )
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS ad_alert_deliveries ("
                "id SERIAL PRIMARY KEY,"
                "user_id BIGINT NOT NULL,"
                "ad_id INTEGER NOT NULL,"
                "delivered_at TIMESTAMPTZ DEFAULT now(),"
                "UNIQUE(user_id, ad_id)"
                ")"
            )
        )
        await session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_ad_alert_subscriptions_user "
                "ON ad_alert_subscriptions (user_id)"
            )
        )
    await session.commit()
    session.info["ad_alerts_ready"] = True


async def list_subscriptions(
    sessionmaker: async_sessionmaker, user_id: int
) -> list[dict]:
    async with sessionmaker() as session:
        await _ensure_tables(session)
        result = await session.execute(
            text(
                "SELECT id, game_id, price_min, price_max, server_query, active "
                "FROM ad_alert_subscriptions "
                "WHERE user_id = :user_id "
                "ORDER BY id DESC"
            ),
            {"user_id": user_id},
        )
        return [dict(row) for row in result.mappings().all()]


async def create_subscription(
    sessionmaker: async_sessionmaker,
    *,
    user_id: int,
    game_id: int | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
    server_query: str | None,
) -> int | None:
    async with sessionmaker() as session:
        await _ensure_tables(session)
        dialect = session.bind.dialect.name
        count_result = await session.execute(
            text(
                "SELECT COUNT(*) FROM ad_alert_subscriptions "
                "WHERE user_id = :user_id AND active = :active"
            ),
            {"user_id": user_id, "active": True},
        )
        current = count_result.scalar_one() or 0
        if current >= MAX_SUBSCRIPTIONS:
            return None

        params = {
            "user_id": user_id,
            "game_id": game_id,
            "price_min": price_min,
            "price_max": price_max,
            "server_query": server_query,
            "active": True,
        }
        if dialect == "sqlite":
            await session.execute(
                text(
                    "INSERT INTO ad_alert_subscriptions "
                    "(user_id, game_id, price_min, price_max, server_query, active) "
                    "VALUES (:user_id, :game_id, :price_min, :price_max, :server_query, :active)"
                ),
                params,
            )
            result = await session.execute(text("SELECT last_insert_rowid()"))
            sub_id = result.scalar_one()
        else:
            result = await session.execute(
                text(
                    "INSERT INTO ad_alert_subscriptions "
                    "(user_id, game_id, price_min, price_max, server_query, active) "
                    "VALUES (:user_id, :game_id, :price_min, :price_max, :server_query, :active) "
                    "RETURNING id"
                ),
                params,
            )
            sub_id = result.scalar_one()
        await session.commit()
        return sub_id


async def delete_subscription(
    sessionmaker: async_sessionmaker, *, user_id: int, sub_id: int
) -> bool:
    async with sessionmaker() as session:
        await _ensure_tables(session)
        result = await session.execute(
            text(
                "DELETE FROM ad_alert_subscriptions "
                "WHERE id = :sub_id AND user_id = :user_id"
            ),
            {"sub_id": sub_id, "user_id": user_id},
        )
        await session.commit()
        return result.rowcount > 0


def _price_matches(
    price: Decimal | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
) -> bool:
    if price is None:
        return price_min is None and price_max is None
    if price_min is not None and price < price_min:
        return False
    if price_max is not None and price > price_max:
        return False
    return True


def _server_matches(query: str | None, text_value: str) -> bool:
    if not query:
        return True
    tokens = [token for token in query.split() if token]
    if not tokens:
        return True
    haystack = text_value.casefold()
    return all(token.casefold() in haystack for token in tokens)


async def notify_ad_alerts_for_ad(
    bot,
    sessionmaker: async_sessionmaker,
    ad_id: int,
) -> None:
    async with sessionmaker() as session:
        await _ensure_tables(session)
        result = await session.execute(
            select(Ad, Game).join(Game, Game.id == Ad.game_id).where(Ad.id == ad_id)
        )
        row = result.first()
        if not row:
            return
        ad, game = row
        if ad.ad_kind != "sale" or not ad.active or ad.moderation_status != "approved":
            return

        delivered_result = await session.execute(
            text("SELECT user_id FROM ad_alert_deliveries WHERE ad_id = :ad_id"),
            {"ad_id": ad_id},
        )
        delivered = {row[0] for row in delivered_result.all() if row and row[0]}

        subs_result = await session.execute(
            text(
                "SELECT id, user_id, game_id, price_min, price_max, server_query "
                "FROM ad_alert_subscriptions "
                "WHERE active = :active"
            ),
            {"active": True},
        )
        subscriptions = [dict(row) for row in subs_result.mappings().all()]

    if not subscriptions:
        return

    title_html = html.escape(ad.title or "")
    description_html = html.escape(ad.description or "")
    game_name = html.escape(game.name) if game else "-"
    price_label = (
        f"{ad.price:,.2f}".replace(",", " ") + " ₽" if ad.price is not None else "Договорная"
    )
    base_text = (
        "🔔 <b>Новое объявление по твоей подписке</b>\n"
        f"🎮 Игра: {game_name}\n"
        f"💰 Цена: {price_label}\n"
        f"🏷 {title_html}\n"
        f"{description_html}\n"
        f"🆔 Объявление: {ad.id}"
    )
    actions_kb = ad_actions_kb(ad.id)
    search_text = f"{ad.title or ''} {ad.description or ''} {ad.account_id or ''}"

    for sub in subscriptions:
        user_id = sub.get("user_id")
        if not user_id or user_id == ad.seller_id or user_id in delivered:
            continue
        if sub.get("game_id") and ad.game_id != sub["game_id"]:
            continue
        if not _price_matches(ad.price, sub.get("price_min"), sub.get("price_max")):
            continue
        if not _server_matches(sub.get("server_query"), search_text):
            continue

        try:
            if ad.media_type == "фото" and ad.media_file_id:
                await bot.send_photo(
                    user_id,
                    ad.media_file_id,
                    caption=base_text,
                    reply_markup=actions_kb,
                    parse_mode="HTML",
                )
            elif ad.media_type == "видео" and ad.media_file_id:
                await bot.send_video(
                    user_id,
                    ad.media_file_id,
                    caption=base_text,
                    reply_markup=actions_kb,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    user_id,
                    base_text,
                    reply_markup=actions_kb,
                    parse_mode="HTML",
                )
        except (TelegramForbiddenError, TelegramBadRequest):
            continue
        except Exception:
            continue

        async with sessionmaker() as session:
            await _ensure_tables(session)
            if session.bind.dialect.name == "sqlite":
                await session.execute(
                    text(
                        "INSERT OR IGNORE INTO ad_alert_deliveries (user_id, ad_id) "
                        "VALUES (:user_id, :ad_id)"
                    ),
                    {"user_id": user_id, "ad_id": ad.id},
                )
            else:
                await session.execute(
                    text(
                        "INSERT INTO ad_alert_deliveries (user_id, ad_id) "
                        "VALUES (:user_id, :ad_id) "
                        "ON CONFLICT (user_id, ad_id) DO NOTHING"
                    ),
                    {"user_id": user_id, "ad_id": ad.id},
                )
            await session.commit()
