"""Pydantic-схемы для чата клиент↔отель.

См. карта `open_cards/cards/booking/feature/2026-05-28-client-hotel-chat.md`
(R10 за полной схемой БД).
"""
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.models import ChatSenderKind, ChatSubjectType


class OpenThreadRequest(BaseModel):
    """Открыть (get-or-create) тред с отелем.

    `subject_*` опциональны — это контекст «откуда зашли» (карточка
    брони/комнаты/view отеля), используется в подсветке `SubjectCard`
    на фронте и как default для нового сообщения.
    """

    hotel_id: int
    subject_type: ChatSubjectType | None = None
    subject_id: int | None = None


class HotelMini(BaseModel):
    id: int
    slug: str
    name_ru: str
    photo: str | None = None


class ThreadView(BaseModel):
    id: int
    hotel: HotelMini
    last_message_at: datetime | None
    last_message_body: str | None = None
    last_message_sender_kind: ChatSenderKind | None = None
    unread_for_client: bool
    unread_for_hotel: bool


class MessageView(BaseModel):
    id: int
    thread_id: int
    sender_kind: ChatSenderKind
    subject_type: ChatSubjectType | None
    subject_id: int | None
    body: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    subject_type: ChatSubjectType | None = None
    subject_id: int | None = None


class MessagesPage(BaseModel):
    items: list[MessageView]
    next_cursor: int | None = None
