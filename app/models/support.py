"""Support chat — модели (карта #92).

Заменили полноценный Zendesk-style тикетинг
(history карта 2026-06-02-support-ticketing-system.md) на минимальный
чат user↔admin. Thread keyed по (user_id, block): один пользователь
может иметь два независимых thread'а как client и как partner.

Старые таблицы (10 шт. + sequence + 5 enum'ов) удаляются миграцией
20260612_support_simplify_to_chat.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.models.models import Base


class SupportBlock(str, enum.Enum):
    client = "client"
    partner = "partner"


class SupportSenderKind(str, enum.Enum):
    user = "user"
    admin = "admin"


class SupportThread(Base):
    """Чат-канал пользователя в одном блоке (client/partner).

    Создаётся лениво при первом сообщении. UNIQUE(user_id, block)
    гарантирует один thread на пару.
    """

    __tablename__ = "support_thread"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    block: Mapped[SupportBlock] = mapped_column(
        ENUM(SupportBlock, name="support_block", create_type=False),
        nullable=False,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "block", name="uq_support_thread_user_block"),
        Index("ix_support_thread_last_msg", "last_message_at"),
        Index("ix_support_thread_user", "user_id"),
    )


class SupportMessage(Base):
    """Сообщение в support-чате.

    sender_kind различает user/admin. sender_user_id всегда указывает на
    конкретного отправителя — для admin это столбец позволяет видеть
    «какой именно admin ответил».
    """

    __tablename__ = "support_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("support_thread.id", ondelete="CASCADE"), nullable=False
    )
    sender_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sender_kind: Mapped[SupportSenderKind] = mapped_column(
        ENUM(SupportSenderKind, name="support_sender_kind", create_type=False),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_support_message_thread_created", "thread_id", "created_at"),
    )
