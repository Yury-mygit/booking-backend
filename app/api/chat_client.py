"""Client-side chat endpoints (/c/chat/*).

См. карта `open_cards/cards/booking/feature/2026-05-28-client-hotel-chat.md`.

- POST /c/chat/threads/open — get-or-create тред с отелем.
- GET  /c/chat/threads — inbox: список тредов клиента (последнее сообщение
  + unread флаг + hotel mini).
- GET  /c/chat/threads/{id}/messages?cursor=&limit= — список сообщений.
- POST /c/chat/threads/{id}/messages — отправить сообщение.
- POST /c/chat/threads/{id}/read — отметить тред прочитанным.

Все endpoints требуют залогиненного клиента (`require_role(UserRole.client)`).
Доступ к треду — только владелец `thread.client_user_id == ctx.user.id`.
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import chat_pubsub
from app.core.database import get_db
from app.core.deps import AuthContext, _resolve_session, require_role
from app.core.exceptions import APIError
from app.models.models import (
    ChatMessage,
    ChatSenderKind,
    ChatThread,
    Hotel,
    UserRole,
)
from app.schemas.chat import (
    HotelMini,
    MessagesPage,
    MessageView,
    OpenThreadRequest,
    SendMessageRequest,
    ThreadView,
)
from app.services import chat as chat_service

router = APIRouter(prefix="/c/chat", tags=["client", "chat"])

_HEARTBEAT_SECONDS = 30


def _hotel_to_mini(h: Hotel) -> HotelMini:
    photo = h.photos[0] if h.photos else None
    return HotelMini(id=h.id, slug=h.slug, name_ru=h.name_ru, photo=photo)


def _thread_view(
    th: ChatThread, h: Hotel, last_msg: ChatMessage | None
) -> ThreadView:
    return ThreadView(
        id=th.id,
        hotel=_hotel_to_mini(h),
        last_message_at=th.last_message_at,
        last_message_body=last_msg.body if last_msg else None,
        last_message_sender_kind=last_msg.sender_kind if last_msg else None,
        unread_for_client=chat_service.is_unread_for(th, ChatSenderKind.client),
        unread_for_hotel=chat_service.is_unread_for(th, ChatSenderKind.hotel),
    )


def _msg_to_view(m: ChatMessage) -> MessageView:
    return MessageView(
        id=m.id,
        thread_id=m.thread_id,
        sender_kind=m.sender_kind,
        subject_type=m.subject_type,
        subject_id=m.subject_id,
        body=m.body,
        created_at=m.created_at,
    )


async def _own_thread(
    db: AsyncSession, thread_id: int, ctx: AuthContext
) -> ChatThread:
    th = (
        await db.execute(select(ChatThread).where(ChatThread.id == thread_id))
    ).scalar_one_or_none()
    if th is None:
        raise APIError(404, "not_found", "Thread not found")
    if th.client_user_id != ctx.user.id:
        raise APIError(403, "forbidden", "Not your thread")
    return th


@router.post("/threads/open", response_model=ThreadView)
async def open_thread(
    payload: OpenThreadRequest,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> ThreadView:
    thread = await chat_service.get_or_create_thread(
        db, hotel_id=payload.hotel_id, client_user_id=ctx.user.id
    )
    hotel = (
        await db.execute(select(Hotel).where(Hotel.id == thread.hotel_id))
    ).scalar_one()
    last = (await chat_service.last_messages_for(db, [thread.id])).get(thread.id)
    return _thread_view(thread, hotel, last)


@router.get("/threads", response_model=list[ThreadView])
async def list_threads(
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> list[ThreadView]:
    rows = (
        await db.execute(
            select(ChatThread, Hotel)
            .join(Hotel, Hotel.id == ChatThread.hotel_id)
            .where(ChatThread.client_user_id == ctx.user.id)
            .order_by(desc(ChatThread.last_message_at))
        )
    ).all()
    last_map = await chat_service.last_messages_for(db, [th.id for th, _ in rows])
    return [_thread_view(th, h, last_map.get(th.id)) for th, h in rows]


@router.get("/threads/{thread_id}/messages", response_model=MessagesPage)
async def list_messages(
    thread_id: int,
    cursor: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> MessagesPage:
    await _own_thread(db, thread_id, ctx)
    items, next_cursor = await chat_service.list_messages(
        db, thread_id, cursor, limit
    )
    return MessagesPage(
        items=[_msg_to_view(m) for m in items],
        next_cursor=next_cursor,
    )


@router.post("/threads/{thread_id}/messages", response_model=MessageView, status_code=201)
async def send_message(
    thread_id: int,
    payload: SendMessageRequest,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> MessageView:
    th = await _own_thread(db, thread_id, ctx)
    msg = await chat_service.append_message(
        db,
        thread=th,
        sender_kind=ChatSenderKind.client,
        sender_user_id=ctx.user.id,
        body=payload.body,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
    )
    return _msg_to_view(msg)


@router.post("/threads/{thread_id}/read", status_code=204)
async def mark_read(
    thread_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> None:
    th = await _own_thread(db, thread_id, ctx)
    await chat_service.mark_read(db, th, ChatSenderKind.client)


@router.get("/events")
async def chat_events(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE: события чата для всех тредов клиента (topic `client:<user_id>`).

    Auth: `Authorization: Bearer <t>` ИЛИ `?token=<t>` (EventSource не умеет
    кастомные headers — query-fallback нужен фронту).
    Heartbeat 30с, retry 5с. Caddy `book.dev` блок выставляет `flush_interval -1`.
    """
    if not authorization and token:
        authorization = f"Bearer {token}"
    ctx = await _resolve_session(authorization, db)
    topic = f"client:{ctx.user.id}"

    async def gen():
        yield "retry: 5000\n\n"
        sub = chat_pubsub.subscribe_many([topic])
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        sub.__anext__(), timeout=_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            await sub.aclose()

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
