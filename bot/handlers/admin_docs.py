"""Module for admin docs functionality."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.handlers.helpers import get_or_create_user
from bot.utils.admin_target import get_admin_target
from bot.utils.roles import is_staff, is_owner

router = Router()


def _docs_text() -> str:
    """Handle docs text.

    Returns:
        Return value.
    """
    return """<b>📘 Документация GSNS Trade</b>
Ниже — список команд, доступных в админ-чате.

<b>🧭 Базовые команды</b>
/start — запуск бота
/id — получить CHAT_ID и TOPIC_ID
/cancel — сбросить состояние диалога
/support — связаться с поддержкой

<b>📌 Админ-ветка</b>
/set_admin_topic [TOPIC_ID] — задать топик для логов
/admin_target — показать текущую админ-ветку
/clear_admin_topic — сбросить на общий чат
/ping_admin — тест отправки в админ-чат

<b>👑 Персонал</b>
/set_role user_id role — назначить роль
Доступ: только owner и главный админ.
Роли: admin | moderator | designer | guarantor
Owner назначать нельзя, user не назначается вручную.

<b>🤝 Сделки</b>
/create_deal buyer seller price [type] [addon] — ручная сделка
/create_deal seller price [type] [addon] — указать buyer дальше
type: buy | contact | exchange | exchange_with_addon | installment
addon — сумма доплаты для exchange_with_addon

<b>💎 VIP</b>
/set_vip user_id days — выдать VIP на N дней (0 = снять)
Доступ: только owner и главный админ.

<b>📢 Рассылка</b>
/broadcast текст — заявка на рассылку

<b>🧭 Trust Score</b>
/trust_freeze user_id [причина] — заморозить Trust
/trust_unfreeze user_id [причина] — разморозить Trust
/trust_rollback event_id — откатить событие Trust
/verify_user user_id — верификация (+5 Trust)
/unverify_user user_id — снять верификацию (-5 Trust)
/resolve_dispute dispute_id buyer|seller — отметить проигравшего (-15 Trust)

<b>🛡 Поддержка</b>
/support_close ticket_id — закрыть тикет

<b>💡 Примечания</b>
• Команды доступны персоналу и владельцу.
• В админ-чате можно вызвать /admin_docs для справки.
"""


@router.message(F.text == "/admin_docs")
async def admin_docs(
    message: Message,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    """Handle admin docs.

    Args:
        message: Value for message.
        sessionmaker: Value for sessionmaker.
        settings: Value for settings.
    """
    chat_id, _ = get_admin_target(settings)
    if message.chat.id != chat_id:
        async with sessionmaker() as session:
            user = await get_or_create_user(session, message.from_user)
            if not is_staff(user.role) and not is_owner(
                user.role, settings.owner_ids, user.id
            ):
                return
    await message.answer(_docs_text())
