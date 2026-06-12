"""Pydantic-схемы support-чата (карта #92).

Минимум для user-side и admin-side. User и admin видят одинаковые
сообщения (`is_internal` нет), различается только список thread'ов:
user — свой по `block`; admin — полный список thread'ов с краткой
карточкой user'а.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.support import SupportBlock, SupportSenderKind


class UserMini(BaseModel):
    """Минимальный профиль для thread-list в admin."""

    id: int
    telegram_id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    id: int
    thread_id: int
    sender_user_id: int
    sender_kind: SupportSenderKind
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageCreateUser(BaseModel):
    """user → POST /api/v1/support/thread/messages body."""

    block: SupportBlock
    body: str = Field(min_length=1, max_length=8000)


class MessageCreateAdmin(BaseModel):
    """admin → POST /api/v1/admin/support/threads/{id}/messages body."""

    body: str = Field(min_length=1, max_length=8000)


class ThreadOutUser(BaseModel):
    """Что user видит про свой thread."""

    id: int
    block: SupportBlock
    last_message_at: datetime | None
    has_unread: bool

    model_config = ConfigDict(from_attributes=True)


class ThreadOutAdmin(BaseModel):
    """Карточка thread'а в admin-списке + детали по thread'у."""

    id: int
    block: SupportBlock
    user: UserMini
    last_message_at: datetime | None
    last_message_preview: str | None
    unread_count: int

    model_config = ConfigDict(from_attributes=True)


class ReadMarkUser(BaseModel):
    """user → POST /api/v1/support/thread/read body."""

    block: SupportBlock
    up_to_message_id: int | None = None


class ReadMarkAdmin(BaseModel):
    """admin → POST /api/v1/admin/support/threads/{id}/read body."""

    up_to_message_id: int | None = None
