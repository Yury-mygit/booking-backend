"""TBB-31: заявка клиента на отмену подтверждённого бронирования.

Не меняет booking.status. Создаёт системное сообщение в чате брони
(client↔hotel thread, kind=cancellation_request). Dedup — по наличию
такого сообщения в треде. Партнёрский маркер строится тем же запросом
в /p/bookings.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.exceptions import APIError
from app.models.models import (
    Booking,
    ChatMessage,
    ChatMessageKind,
    ChatSenderKind,
    ChatSubjectType,
    ChatThread,
    Hotel,
    Room,
    User,
)
from app.services import chat as chat_service


# Enum кодов причин (см. TBB-31 decision § Q-reasons-list).
REASON_CODES = (
    "plans_changed",
    "found_better",
    "booking_error",
    "partner_issue",
    "other",
)

REASON_LABELS: dict[str, str] = {
    "plans_changed": "Планы изменились",
    "found_better": "Нашёл вариант лучше",
    "booking_error": "Ошибка при бронировании",
    "partner_issue": "Проблема с отелем",
    "other": "Другое",
}

HEADER = "Запрос на отмену бронирования"
NOTE_PREFIX = "Комментарий"


def _build_body(reasons: list[str], note: str | None) -> str:
    """Формат тела системки: заголовок + машиночитаемая CSV + человеческая строка.
    Опционально третья строка с note (после разделителя).
    """
    human = "; ".join(REASON_LABELS[code] for code in reasons)
    lines = [
        HEADER,
        f"reasons={','.join(reasons)}",
        human,
    ]
    if note:
        lines.append(f"{NOTE_PREFIX}: {note.strip()}")
    return "\n".join(lines)


async def _existing_request(
    db: AsyncSession, thread_id: int
) -> ChatMessage | None:
    return (
        await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.kind == ChatMessageKind.cancellation_request,
            )
            .order_by(ChatMessage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def create_cancellation_request(
    db: AsyncSession,
    booking: Booking,
    reasons: list[str],
    note: str | None,
    client_user: User,
) -> ChatMessage:
    """Создать заявку. Ошибки:
    - 409 `cancellation_already_requested` — заявка уже есть в чате.
    Возвращает созданное ChatMessage.
    """
    # Разрешаем причины ровно из enum'а.
    if not reasons or any(r not in REASON_CODES for r in reasons):
        raise APIError(422, "bad_request", "Invalid reasons")
    if "other" in reasons and not (note and note.strip()):
        raise APIError(422, "bad_request", "note is required when 'other' is selected")

    # hotel_id брони — через Room.
    hotel_id = (
        await db.execute(
            select(Room.hotel_id).where(Room.id == booking.room_id)
        )
    ).scalar_one()

    thread = await chat_service.get_or_create_thread(
        db, hotel_id=hotel_id, client_user_id=client_user.id
    )

    prev = await _existing_request(db, thread.id)
    if prev is not None:
        raise APIError(
            409,
            "cancellation_already_requested",
            "Cancellation already requested",
            detail={"requested_at": prev.created_at.isoformat()},
        )

    body = _build_body(reasons, note)

    msg = await chat_service.append_message(
        db,
        thread=thread,
        sender_kind=ChatSenderKind.client,
        sender_user_id=client_user.id,
        body=body,
        subject_type=ChatSubjectType.booking,
        subject_id=booking.id,
        kind=ChatMessageKind.cancellation_request,
    )

    # Партнёрский листинг перечитывает bookings — маркер «⚠ запрошена
    # отмена» появится через has_cancellation_request флаг.
    await pubsub.publish_refresh(hotel_id)
    return msg
