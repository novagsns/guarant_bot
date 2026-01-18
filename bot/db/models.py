"""Module for models functionality."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from bot.db.base import Base


class User(Base):
    """Represent User.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        username: Attribute value.
        full_name: Attribute value.
        role: Attribute value.
        balance: Attribute value.
        rating_avg: Attribute value.
        rating_count: Attribute value.
        verified: Attribute value.
        on_shift: Attribute value.
        referrer_id: Attribute value.
        vip_until: Attribute value.
        free_fee_until: Attribute value.
        ban_until: Attribute value.
        paid_broadcasts_date: Attribute value.
        paid_broadcasts_count: Attribute value.
        created_at: Attribute value.
        ads: Attribute value.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="user")
    balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    rating_avg: Mapped[float | None] = mapped_column(Numeric(3, 2))
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    on_shift: Mapped[bool] = mapped_column(Boolean, default=False)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    vip_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    free_fee_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ban_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_broadcasts_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    paid_broadcasts_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    ads: Mapped[list["Ad"]] = relationship(back_populates="seller")


class Game(Base):
    """Represent Game.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        name: Attribute value.
        active: Attribute value.
        created_at: Attribute value.
        ads: Attribute value.
    """

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    ads: Mapped[list["Ad"]] = relationship(back_populates="game")


class Ad(Base):
    """Represent Ad.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        seller_id: Attribute value.
        game_id: Attribute value.
        ad_kind: Attribute value.
        title: Attribute value.
        description: Attribute value.
        price: Attribute value.
        currency: Attribute value.
        payment_methods: Attribute value.
        account_id: Attribute value.
        media_type: Attribute value.
        media_file_id: Attribute value.
        active: Attribute value.
        moderation_status: Attribute value.
        promoted_at: Attribute value.
        created_at: Attribute value.
        seller: Attribute value.
        game: Attribute value.
    """

    __tablename__ = "ads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    ad_kind: Mapped[str] = mapped_column(String(16), default="sale")
    title: Mapped[str] = mapped_column(String(120))
    title_html: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    description_html: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    payment_methods: Mapped[str | None] = mapped_column(String(64))
    account_id: Mapped[str | None] = mapped_column(String(64))
    media_type: Mapped[str | None] = mapped_column(String(16))
    media_file_id: Mapped[str | None] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    moderation_status: Mapped[str] = mapped_column(String(16), default="approved")
    moderation_reason: Mapped[str | None] = mapped_column(Text)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    seller: Mapped[User] = relationship(back_populates="ads")
    game: Mapped[Game] = relationship(back_populates="ads")


class Deal(Base):
    """Represent Deal.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        ad_id: Attribute value.
        buyer_id: Attribute value.
        seller_id: Attribute value.
        guarantee_id: Attribute value.
        status: Attribute value.
        deal_type: Attribute value.
        price: Attribute value.
        fee: Attribute value.
        room_chat_id: Attribute value.
        room_invite_link: Attribute value.
        room_ready: Attribute value.
        closed_at: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_id: Mapped[int | None] = mapped_column(ForeignKey("ads.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    guarantee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default="requested")
    deal_type: Mapped[str] = mapped_column(String(32), default="buy")
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    fee: Mapped[float | None] = mapped_column(Numeric(12, 2))
    room_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    room_invite_link: Mapped[str | None] = mapped_column(Text)
    room_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DealRoom(Base):
    """Represent DealRoom.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        title: Attribute value.
        invite_link: Attribute value.
        active: Attribute value.
        created_by: Attribute value.
        assigned_deal_id: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "deal_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str | None] = mapped_column(String(255))
    invite_link: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    assigned_deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DealMessage(Base):
    """Represent DealMessage.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        deal_id: Attribute value.
        sender_id: Attribute value.
        sender_role: Attribute value.
        message_type: Attribute value.
        text: Attribute value.
        file_id: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "deal_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"))
    sender_id: Mapped[int] = mapped_column(BigInteger)
    sender_role: Mapped[str] = mapped_column(String(16))
    message_type: Mapped[str] = mapped_column(String(16), default="text")
    text: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationChat(Base):
    """Represent ModerationChat.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        title: Attribute value.
        active: Attribute value.
        added_by: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationWord(Base):
    """Represent ModerationWord.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        word: Attribute value.
        active: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    word: Mapped[str] = mapped_column(String(255), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationStickerPack(Base):
    """Represent ModerationStickerPack.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        set_name: Attribute value.
        active: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_sticker_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    set_name: Mapped[str] = mapped_column(String(255), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationCustomEmoji(Base):
    """Represent ModerationCustomEmoji.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        emoji_id: Attribute value.
        active: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_custom_emojis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    emoji_id: Mapped[str] = mapped_column(String(128), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationCustomEmojiPack(Base):
    """Represent ModerationCustomEmojiPack.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        set_name: Attribute value.
        title: Attribute value.
        active: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_custom_emoji_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    set_name: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationCase(Base):
    """Represent ModerationCase.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        kind: Attribute value.
        chat_id: Attribute value.
        user_id: Attribute value.
        payload: Attribute value.
        prev_role: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[str | None] = mapped_column(Text)
    prev_role: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationMemberEvent(Base):
    """Represent ModerationMemberEvent.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        user_id: Attribute value.
        event_type: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_member_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationRestriction(Base):
    """Represent ModerationRestriction.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        user_id: Attribute value.
        action: Attribute value.
        reason: Attribute value.
        until_date: Attribute value.
        created_by: Attribute value.
        active: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "moderation_restrictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(255))
    until_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationWarn(Base):
    """Represent ModerationWarn.

    Attributes:
        id: Attribute value.
        chat_id: Attribute value.
        user_id: Attribute value.
        count: Attribute value.
        created_at: Attribute value.
        updated_at: Attribute value.
    """

    __tablename__ = "moderation_warns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class TrustState(Base):
    """Represent TrustState.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        score: Attribute value.
        frozen: Attribute value.
        cap: Attribute value.
        last_activity_month: Attribute value.
        updated_at: Attribute value.
    """

    __tablename__ = "trust_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    cap: Mapped[int] = mapped_column(Integer, default=100)
    last_activity_month: Mapped[str | None] = mapped_column(String(7))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TrustEvent(Base):
    """Represent TrustEvent.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        event_type: Attribute value.
        delta: Attribute value.
        reason: Attribute value.
        ref_type: Attribute value.
        ref_id: Attribute value.
        applied: Attribute value.
        reversed: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "trust_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(255))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    applied: Mapped[bool] = mapped_column(Boolean, default=True)
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserAction(Base):
    """Represent UserAction.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        action_type: Attribute value.
        action: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "user_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action_type: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WeeklyReward(Base):
    """Represent WeeklyReward.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        week_start: Attribute value.
        user_id: Attribute value.
        amount: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "weekly_rewards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CoinDrop(Base):
    """Represent CoinDrop.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        topic_id: Attribute value.
        message_id: Attribute value.
        created_by: Attribute value.
        claimed_by: Attribute value.
        claimed_username: Attribute value.
        amount: Attribute value.
        credited: Attribute value.
        created_at: Attribute value.
        claimed_at: Attribute value.
        credited_at: Attribute value.
    """

    __tablename__ = "coin_drops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    topic_id: Mapped[int | None] = mapped_column(Integer)
    message_id: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(BigInteger)
    claimed_by: Mapped[int | None] = mapped_column(BigInteger)
    claimed_username: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[int | None] = mapped_column(Integer)
    credited: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TopicActivityMeta(Base):
    """Represent TopicActivityMeta.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        topic_id: Attribute value.
        pinned_message_id: Attribute value.
        period_start: Attribute value.
        last_reward_at: Attribute value.
        updated_at: Attribute value.
    """

    __tablename__ = "topic_activity_meta"
    __table_args__ = (
        UniqueConstraint("chat_id", "topic_id", name="uq_topic_activity_meta"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    topic_id: Mapped[int] = mapped_column(Integer)
    pinned_message_id: Mapped[int | None] = mapped_column(Integer)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reward_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TopicActivityStat(Base):
    """Represent TopicActivityStat.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        topic_id: Attribute value.
        user_id: Attribute value.
        username: Attribute value.
        full_name: Attribute value.
        message_count: Attribute value.
        last_counted_at: Attribute value.
        created_at: Attribute value.
        updated_at: Attribute value.
    """

    __tablename__ = "topic_activity_stats"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "topic_id",
            "user_id",
            name="uq_topic_activity_stat",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    topic_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(128))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TopicActivityReward(Base):
    """Represent TopicActivityReward.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        chat_id: Attribute value.
        topic_id: Attribute value.
        user_id: Attribute value.
        amount: Attribute value.
        status: Attribute value.
        period_start: Attribute value.
        created_at: Attribute value.
        granted_at: Attribute value.
    """

    __tablename__ = "topic_activity_rewards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    topic_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WalletTransaction(Base):
    """Represent WalletTransaction.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        amount: Attribute value.
        type: Attribute value.
        description: Attribute value.
        ref_type: Attribute value.
        ref_id: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    type: Mapped[str] = mapped_column(String(24))
    description: Mapped[str | None] = mapped_column(String(255))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TopUp(Base):
    """Represent TopUp.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        amount: Attribute value.
        amount_rub: Attribute value.
        amount_usdt: Attribute value.
        receipt_file_id: Attribute value.
        status: Attribute value.
        reason: Attribute value.
        reviewer_id: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "topups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    amount_rub: Mapped[float | None] = mapped_column(Numeric(14, 2))
    amount_usdt: Mapped[float | None] = mapped_column(Numeric(14, 6))
    receipt_file_id: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reason: Mapped[str | None] = mapped_column(String(255))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Service(Base):
    """Represent Service.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        creator_id: Attribute value.
        category: Attribute value.
        title: Attribute value.
        description: Attribute value.
        price: Attribute value.
        media_type: Attribute value.
        media_file_id: Attribute value.
        active: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Numeric(14, 2))
    media_type: Mapped[str | None] = mapped_column(String(16))
    media_file_id: Mapped[str | None] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ServicePurchase(Base):
    """Represent ServicePurchase.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        service_id: Attribute value.
        buyer_id: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "service_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SupportTicket(Base):
    """Represent SupportTicket.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        assignee_id: Attribute value.
        status: Attribute value.
        last_message: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="open")
    last_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SupportMessage(Base):
    """Represent SupportMessage.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        ticket_id: Attribute value.
        sender_id: Attribute value.
        text: Attribute value.
        media_type: Attribute value.
        file_id: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(16))
    file_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Scammer(Base):
    """Represent Scammer.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        username: Attribute value.
        account_id: Attribute value.
        account_details: Attribute value.
        payment_details: Attribute value.
        notes: Attribute value.
        created_by: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "scammers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    account_id: Mapped[str | None] = mapped_column(String(64))
    account_details: Mapped[str | None] = mapped_column(Text)
    payment_details: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScammerEvidence(Base):
    """Represent ScammerEvidence.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        scammer_id: Attribute value.
        media_type: Attribute value.
        file_id: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "scammer_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scammer_id: Mapped[int] = mapped_column(ForeignKey("scammers.id"))
    media_type: Mapped[str] = mapped_column(String(16))
    file_id: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BroadcastRequest(Base):
    """Represent BroadcastRequest.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        creator_id: Attribute value.
        ad_id: Attribute value.
        kind: Attribute value.
        text: Attribute value.
        cost: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "broadcast_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ad_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RouletteSpin(Base):
    """Represent RouletteSpin.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        user_id: Attribute value.
        cost: Attribute value.
        prize_type: Attribute value.
        prize_amount: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "roulette_spins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    prize_type: Mapped[str] = mapped_column(String(16), default="none")
    prize_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Review(Base):
    """Represent Review.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        deal_id: Attribute value.
        author_id: Attribute value.
        target_id: Attribute value.
        rating: Attribute value.
        comment: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint(
            "deal_id",
            "author_id",
            "target_id",
            name="uq_reviews_deal_author_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Dispute(Base):
    """Represent Dispute.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        deal_id: Attribute value.
        reporter_id: Attribute value.
        winner_id: Attribute value.
        description: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "disputes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"))
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    winner_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Complaint(Base):
    """Represent Complaint.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        ad_id: Attribute value.
        reporter_id: Attribute value.
        reason: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"))
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StaffTask(Base):
    """Represent StaffTask.

    Attributes:
        __tablename__: Attribute value.
        id: Attribute value.
        assignee_id: Attribute value.
        creator_id: Attribute value.
        title: Attribute value.
        description: Attribute value.
        status: Attribute value.
        created_at: Attribute value.
    """

    __tablename__ = "staff_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
