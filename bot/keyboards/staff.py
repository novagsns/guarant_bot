"""Module for staff functionality."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def owner_panel_kb() -> InlineKeyboardMarkup:
    """Handle owner panel kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="?? ?????????? ??????????", callback_data="owner:staff"
                ),
                InlineKeyboardButton(text="?? ????", callback_data="owner:roles"),
            ],
            [
                InlineKeyboardButton(
                    text="?? ?????????", callback_data="owner:moderation"
                ),
                InlineKeyboardButton(text="? ?????", callback_data="owner:disputes"),
            ],
            [
                InlineKeyboardButton(text="? ??????", callback_data="owner:reviews"),
                InlineKeyboardButton(
                    text="?? ?????? ?????????", callback_data="owner:design_tasks"
                ),
            ],
            [InlineKeyboardButton(text="?? Trust Score", callback_data="owner:trust")],
            [
                InlineKeyboardButton(
                    text="?? ???? ????????", callback_data="scammers:menu"
                )
            ],
        ]
    )


def admin_panel_kb() -> InlineKeyboardMarkup:
    """Handle admin panel kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="?? ?????????", callback_data="owner:moderation"
                ),
                InlineKeyboardButton(text="? ?????", callback_data="owner:disputes"),
            ],
            [
                InlineKeyboardButton(text="? ??????", callback_data="owner:reviews"),
                InlineKeyboardButton(
                    text="?? ?????? ?????????", callback_data="owner:design_tasks"
                ),
            ],
        ]
    )


def guarantor_panel_kb(on_shift: bool) -> InlineKeyboardMarkup:
    """Handle guarantor panel kb.

    Args:
        on_shift: Value for on_shift.

    Returns:
        Return value.
    """
    shift_label = "?? ?? ?????" if on_shift else "? ?? ?? ?????"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="?? ??? ??????", callback_data="guarantor:deals"
                ),
                InlineKeyboardButton(
                    text="? ??? ??????", callback_data="guarantor:reviews"
                ),
            ],
            [
                InlineKeyboardButton(text="? ????", callback_data="guarantor:dispute"),
                InlineKeyboardButton(text=shift_label, callback_data="guarantor:shift"),
            ],
            [
                InlineKeyboardButton(
                    text="?? ???????? ????????????", callback_data="guarantor:check"
                )
            ],
        ]
    )


def moderator_panel_kb() -> InlineKeyboardMarkup:
    """Handle moderator panel kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="?? ????????? ??????????", callback_data="moderator:ads"
                ),
                InlineKeyboardButton(
                    text="?? ??????", callback_data="moderator:complaints"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="?? ???? ????????", callback_data="scammers:menu"
                )
            ],
        ]
    )


def designer_panel_kb() -> InlineKeyboardMarkup:
    """Handle designer panel kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="?? ??????", callback_data="designer:tasks")]
        ]
    )


def staff_manage_kb() -> InlineKeyboardMarkup:
    """Handle staff manage kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="????????? ????", callback_data="owner:set_role"
                ),
                InlineKeyboardButton(
                    text="?????? ?????????", callback_data="owner:list_staff"
                ),
            ]
        ]
    )


def moderation_ad_kb(ad_id: int) -> InlineKeyboardMarkup:
    """Handle moderation ad kb.

    Args:
        ad_id: Value for ad_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить", callback_data=f"mod_approve:{ad_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"mod_reject:{ad_id}"
                ),
            ]
        ]
    )


def moderation_filter_kb() -> InlineKeyboardMarkup:
    """Handle moderation filter kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏳ Ожидают", callback_data="mod_filter:pending"
                ),
                InlineKeyboardButton(
                    text="✅ Одобрены", callback_data="mod_filter:approved"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонены", callback_data="mod_filter:rejected"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Экспорт: ожидают", callback_data="mod_export:pending"
                ),
                InlineKeyboardButton(
                    text="📄 Экспорт: одобрены", callback_data="mod_export:approved"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📄 Экспорт: отклонены", callback_data="mod_export:rejected"
                ),
                InlineKeyboardButton(
                    text="📄 Экспорт: все", callback_data="mod_export:all"
                ),
            ],
        ]
    )


def complaint_kb(complaint_id: int) -> InlineKeyboardMarkup:
    """Handle complaint kb.

    Args:
        complaint_id: Value for complaint_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"complaint_approve:{complaint_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"complaint_reject:{complaint_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"complaint_delete_req:{complaint_id}",
                )
            ],
        ]
    )


def complaint_filter_kb() -> InlineKeyboardMarkup:
    """Handle complaint filter kb.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📬 Открытые", callback_data="complaint_filter:open"
                ),
                InlineKeyboardButton(
                    text="✅ Закрытые", callback_data="complaint_filter:closed"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📄 Экспорт: открытые", callback_data="complaint_export:open"
                ),
                InlineKeyboardButton(
                    text="📄 Экспорт: закрытые", callback_data="complaint_export:closed"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📄 Экспорт: все", callback_data="complaint_export:all"
                )
            ],
        ]
    )


def review_kb(review_id: int) -> InlineKeyboardMarkup:
    """Handle review kb.

    Args:
        review_id: Value for review_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏ Изменить", callback_data=f"review_edit:{review_id}"
                ),
                InlineKeyboardButton(
                    text="👁 Скрыть", callback_data=f"review_hide:{review_id}"
                ),
            ]
        ]
    )


def review_dispute_kb(review_id: int) -> InlineKeyboardMarkup:
    """Handle review dispute kb.

    Args:
        review_id: Value for review_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚖ Оспорить", callback_data=f"review_dispute:{review_id}"
                )
            ]
        ]
    )


def task_kb(task_id: int, is_owner: bool) -> InlineKeyboardMarkup:
    """Handle task kb.

    Args:
        task_id: Value for task_id.
        is_owner: Value for is_owner.

    Returns:
        Return value.
    """
    if is_owner:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Закрыть", callback_data=f"task_close:{task_id}"
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Готово", callback_data=f"task_done:{task_id}"
                )
            ]
        ]
    )


def guarantor_deal_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Handle guarantor deal kb.

    Args:
        deal_id: Value for deal_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Открыть чат", callback_data=f"chat:{deal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Закрыть", callback_data=f"deal_close_req:{deal_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отменить", callback_data=f"deal_cancel_req:{deal_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚖ Спор", callback_data=f"deal_dispute:{deal_id}"
                )
            ],
        ]
    )


def confirm_deal_action_kb(action: str, deal_id: int) -> InlineKeyboardMarkup:
    """Handle confirm deal action kb.

    Args:
        action: Value for action.
        deal_id: Value for deal_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да", callback_data=f"{action}_yes:{deal_id}"
                ),
                InlineKeyboardButton(
                    text="Нет", callback_data=f"{action}_no:{deal_id}"
                ),
            ]
        ]
    )


def confirm_action_kb(action: str, item_id: int) -> InlineKeyboardMarkup:
    """Handle confirm action kb.

    Args:
        action: Value for action.
        item_id: Value for item_id.

    Returns:
        Return value.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да", callback_data=f"{action}_yes:{item_id}"
                ),
                InlineKeyboardButton(
                    text="Нет", callback_data=f"{action}_no:{item_id}"
                ),
            ]
        ]
    )
