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
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_role
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


def _hotel_to_mini(h: Hotel) -> HotelMini:
    photo = h.photos[0] if h.photos else None
    return HotelMini(id=h.id, slug=h.slug, name_ru=h.name_ru, photo=photo)


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
    return ThreadView(
        id=thread.id,
        hotel=_hotel_to_mini(hotel),
        last_message_at=thread.last_message_at,
        unread_for_client=chat_service.is_unread_for(thread, ChatSenderKind.client),
        unread_for_hotel=chat_service.is_unread_for(thread, ChatSenderKind.hotel),
    )


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
    return [
        ThreadView(
            id=th.id,
            hotel=_hotel_to_mini(h),
            last_message_at=th.last_message_at,
            unread_for_client=chat_service.is_unread_for(th, ChatSenderKind.client),
            unread_for_hotel=chat_service.is_unread_for(th, ChatSenderKind.hotel),
        )
        for th, h in rows
    ]


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
