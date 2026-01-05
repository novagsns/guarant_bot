"""Module for ads functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def game_list_kb(
    games: list[tuple[int, str]],
    *,
    prefix: str = "game",
    page: int = 1,
    total_pages: int = 1,
    include_all: bool = False,
) -> InlineKeyboardMarkup:
    """Handle game list kb.

    Args:
        games: Value for games.
        prefix: Value for prefix.
        page: Value for page.
        total_pages: Value for total_pages.
        include_all: Value for include_all.

    Returns:
        Return value.
    """
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"{prefix}:{game_id}")]
        for game_id, name in games
    ]
    if include_all:
        rows.append(
            [InlineKeyboardButton(text="Все игры", callback_data=f"{prefix}:0")]
        )
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(text="⬅️", callback_data=f"game_page:{page - 1}")
            )
        nav.append(
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop")
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(text="➡️", callback_data=f"game_page:{page + 1}")
            )
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ad_actions_kb(ad_id: int) -> InlineKeyboardMarkup:
    """Handle ad actions kb.

    Args:
        ad_id: Value for ad_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Купить", callback_data=f"buy:{ad_id}"),
                InlineKeyboardButton(
                    text="Связаться", callback_data=f"contact:{ad_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Пожаловаться", callback_data=f"complaint:{ad_id}"
                )
            ],
        ]
    )


def exchange_actions_kb(ad_id: int) -> InlineKeyboardMarkup:
    """Handle exchange actions kb.

    Args:
        ad_id: Value for ad_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Связаться", callback_data=f"contact:{ad_id}"
                ),
                InlineKeyboardButton(
                    text="Обменять", callback_data=f"exchange:{ad_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Пожаловаться", callback_data=f"complaint:{ad_id}"
                )
            ],
        ]
    )


def contact_open_kb(ad_id: int, buyer_id: int) -> InlineKeyboardMarkup:
    """Handle contact open kb.

    Args:
        ad_id: Value for ad_id.
        buyer_id: Value for buyer_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Открыть диалог",
                    callback_data=f"prechat_open:{ad_id}:{buyer_id}",
                )
            ]
        ]
    )


def prechat_finish_kb(ad_id: int) -> InlineKeyboardMarkup:
    """Handle prechat finish kb.

    Args:
        ad_id: Value for ad_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Завершить диалог",
                    callback_data=f"prechat_finish:{ad_id}",
                )
            ]
        ]
    )


def prechat_action_kb(ad_id: int, *, is_exchange: bool = False) -> InlineKeyboardMarkup:
    """Handle prechat action kb.

    Args:
        ad_id: Value for ad_id.
        is_exchange: Value for is_exchange.

    Returns:
        Return value.
    """
    action_text = "🔁 Обменять" if is_exchange else "🛒 Купить"
    action_data = f"prechat_exchange:{ad_id}" if is_exchange else f"prechat_buy:{ad_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=action_text,
                    callback_data=action_data,
                ),
                InlineKeyboardButton(
                    text="❌ Отменить", callback_data=f"prechat_cancel:{ad_id}"
                ),
            ]
        ]
    )


def seller_price_kb(
    ad_id: int, buyer_id: int, price_cents: int
) -> InlineKeyboardMarkup:
    """Handle seller price kb.

    Args:
        ad_id: Value for ad_id.
        buyer_id: Value for buyer_id.
        price_cents: Value for price_cents.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"buy_confirm:{ad_id}:{buyer_id}:{price_cents}",
                ),
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=f"buy_change:{ad_id}:{buyer_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"buy_cancel:{ad_id}:{buyer_id}",
                )
            ],
        ]
    )


def deal_after_take_kb(
    deal_id: int,
    *,
    role: str | None = None,
    guarantor_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Handle deal after take kb.

    Args:
        deal_id: Value for deal_id.
        role: Value for role.
        guarantor_id: Value for guarantor_id.

    Returns:
        Return value.
    """
    rows = [
        [
            InlineKeyboardButton(
                text="💬 Открыть чат",
                callback_data=f"chat:{deal_id}",
            )
        ]
    ]

    if guarantor_id and role in {None, "buyer", "seller"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⭐ Отзывы гаранта",
                    callback_data=f"guarantor_reviews:{deal_id}:{guarantor_id}",
                )
            ]
        )

    if role in {None, "buyer"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔐 Передать данные гаранту",
                    callback_data=f"deal_data:{deal_id}",
                ),
                InlineKeyboardButton(
                    text="💸 Передать оплату гаранту",
                    callback_data=f"deal_payment:{deal_id}",
                ),
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="⚖ Спор", callback_data=f"dispute:{deal_id}")]
        )
    elif role == "seller":
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔐 Передать данные гаранту",
                    callback_data=f"deal_data:{deal_id}",
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="⚖ Спор", callback_data=f"dispute:{deal_id}")]
        )
    elif role == "guarantor":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Завершить сделку",
                    callback_data=f"deal_close_req:{deal_id}",
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="⚖ Спор", callback_data=f"dispute:{deal_id}")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def deal_room_guarantor_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Handle guarantor buttons inside the deal room."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Завершить сделку", callback_data=f"deal_close_req:{deal_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отменить сделку", callback_data=f"deal_cancel_req:{deal_id}"
                ),
            ]
        ]
    )


def my_ad_kb(ad_id: int, active: bool) -> InlineKeyboardMarkup:
    """Handle my ad kb.

    Args:
        ad_id: Value for ad_id.
        active: Value for active.

    Returns:
        Return value.
    """
    if active:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Снять с публикации",
                        callback_data=f"deactivate:{ad_id}",
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Опубликовать", callback_data=f"activate:{ad_id}"
                )
            ]
        ]
    )


def deal_chat_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Handle deal chat kb.

    Args:
        deal_id: Value for deal_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть чат", callback_data=f"chat:{deal_id}")],
            [InlineKeyboardButton(text="Спор", callback_data=f"dispute:{deal_id}")],
        ]
    )


def admin_take_deal_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Handle admin take deal kb.

    Args:
        deal_id: Value for deal_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Взять сделку", callback_data=f"take:{deal_id}")]
        ]
    )


def account_filter_kb(game_id: int | None = None) -> InlineKeyboardMarkup:
    """Handle account filter kb.

    Args:
        game_id: Value for game_id.

    Returns:
        Return value.
    """
    prefix = (
        f"account_filter:{game_id}:" if game_id is not None else "account_filter:0:"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все цены", callback_data=f"{prefix}all")],
            [
                InlineKeyboardButton(
                    text="До 9 999 руб.", callback_data=f"{prefix}0-9999"
                )
            ],
            [
                InlineKeyboardButton(
                    text="10 000-24 999 руб.",
                    callback_data=f"{prefix}10000-24999",
                )
            ],
            [
                InlineKeyboardButton(
                    text="25 000-39 999 руб.",
                    callback_data=f"{prefix}25000-39999",
                )
            ],
            [
                InlineKeyboardButton(
                    text="40 000 руб. и выше",
                    callback_data=f"{prefix}40000+",
                )
            ],
        ]
    )
