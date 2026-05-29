"""Бизнес-логика чата клиент↔отель.

См. карта `open_cards/cards/booking/feature/2026-05-28-client-hotel-chat.md`.

Здесь — get-or-create thread, append message, list (cursor-based),
mark-as-read, rate-limit. REST-обвязка и нотификации — в api-слое.
"""
from collections import defaultdict, deque
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.exceptions import APIError
from app.models.models import (
    ChatMessage,
    ChatSenderKind,
    ChatSubjectType,
    ChatThread,
    Hotel,
)

# Rate-limit: 30 сообщений / час per (sender_kind, user_id) (sliding window).
# In-memory — на restart обнулится; для v1 ОК.
_RATE_WINDOW_SEC = 3600
_RATE_LIMIT = 30
_rate_log: dict[tuple[str, int], deque[float]] = defaultdict(deque)


def _check_rate_limit(sender_kind: ChatSenderKind, user_id: int) -> None:
    key = (sender_kind.value, user_id)
    now = datetime.now(timezone.utc).timestamp()
    bucket = _rate_log[key]
    while bucket and bucket[0] < now - _RATE_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT:
        raise APIError(
            429,
            "rate_limited",
            f"Too many messages (limit {_RATE_LIMIT}/hour)",
        )
    bucket.append(now)


async def get_or_create_thread(
    db: AsyncSession, hotel_id: int, client_user_id: int
) -> ChatThread:
    """Атомарный get-or-create. UNIQUE(hotel_id, client_user_id) гарантирует
    дедупликацию даже при гонке. Возвращает существующий или новый ChatThread.
    """
    existing = (
        await db.execute(
            select(ChatThread).where(
                ChatThread.hotel_id == hotel_id,
                ChatThread.client_user_id == client_user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    hotel = (
        await db.execute(select(Hotel).where(Hotel.id == hotel_id))
    ).scalar_one_or_none()
    if hotel is None:
        raise APIError(404, "not_found", "Hotel not found")

    stmt = (
        pg_insert(ChatThread)
        .values(hotel_id=hotel_id, client_user_id=client_user_id)
        .on_conflict_do_nothing(index_elements=["hotel_id", "client_user_id"])
        .returning(ChatThread.id)
    )
    inserted_id = (await db.execute(stmt)).scalar_one_or_none()
    await db.commit()

    if inserted_id is None:
        # Race won by another tx — re-fetch.
        return (
            await db.execute(
                select(ChatThread).where(
                    ChatThread.hotel_id == hotel_id,
                    ChatThread.client_user_id == client_user_id,
                )
            )
        ).scalar_one()

    return (
        await db.execute(select(ChatThread).where(ChatThread.id == inserted_id))
    ).scalar_one()


async def append_message(
    db: AsyncSession,
    thread: ChatThread,
    sender_kind: ChatSenderKind,
    sender_user_id: int,
    body: str,
    subject_type: ChatSubjectType | None,
    subject_id: int | None,
) -> ChatMessage:
    _check_rate_limit(sender_kind, sender_user_id)

    body = body.strip()
    if not body:
        raise APIError(400, "bad_request", "Empty message body")
    if len(body) > 2000:
        raise APIError(400, "bad_request", "Message body too long (max 2000)")

    msg = ChatMessage(
        thread_id=thread.id,
        sender_kind=sender_kind,
        sender_user_id=sender_user_id,
        subject_type=subject_type,
        subject_id=subject_id,
        body=body,
    )
    db.add(msg)
    await db.flush()

    now = datetime.now(timezone.utc)
    await db.execute(
        update(ChatThread)
        .where(ChatThread.id == thread.id)
        .values(last_message_at=now)
    )
    await db.commit()
    await db.refresh(msg)
    return msg


async def list_messages(
    db: AsyncSession,
    thread_id: int,
    cursor: int | None,
    limit: int,
) -> tuple[list[ChatMessage], int | None]:
    """Cursor-based pagination: cursor = id последнего полученного сообщения
    (берём более старые, как в чатах). Возвращаем (items, next_cursor).
    items идут от новых к старым, фронт развернёт при отображении.
    """
    limit = max(1, min(limit, 100))
    q = select(ChatMessage).where(ChatMessage.thread_id == thread_id)
    if cursor is not None:
        q = q.where(ChatMessage.id < cursor)
    q = q.order_by(ChatMessage.id.desc()).limit(limit + 1)
    rows = (await db.execute(q)).scalars().all()
    next_cursor: int | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = rows[-1].id
    return list(rows), next_cursor


async def mark_read(
    db: AsyncSession, thread: ChatThread, side: ChatSenderKind
) -> None:
    now = datetime.now(timezone.utc)
    if side == ChatSenderKind.client:
        await db.execute(
            update(ChatThread)
            .where(ChatThread.id == thread.id)
            .values(client_last_read_at=now)
        )
    else:
        await db.execute(
            update(ChatThread)
            .where(ChatThread.id == thread.id)
            .values(hotel_last_read_at=now)
        )
    await db.commit()


def is_unread_for(thread: ChatThread, side: ChatSenderKind) -> bool:
    """Тред «непрочитан» если last_message_at новее чем *_last_read_at для
    данной стороны. Если last_message_at = NULL (пустой тред) — непрочитан=False.
    """
    if thread.last_message_at is None:
        return False
    last_read = (
        thread.client_last_read_at
        if side == ChatSenderKind.client
        else thread.hotel_last_read_at
    )
    if last_read is None:
        return True
    return thread.last_message_at > last_read
