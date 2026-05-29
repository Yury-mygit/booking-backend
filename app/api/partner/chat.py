"""Partner-side chat endpoints (/p/clients/{client_id}/chat*).

См. карта `open_cards/cards/booking/feature/2026-05-28-client-hotel-chat.md`.

UI-точка входа: карточка клиента на вкладке «Клиенты» партнёрского SPA.
Партнёр пишет от лица отеля (R2/R5) — клиенту имя сотрудника не видно;
в БД сохраняем настоящего sender + пишем audit_log.

Авторизация: владелец отеля или staff с `perm_chat_with_clients=true`
для конкретного `hotel.owner_user_id`.

Видимость клиента: расширена против стандартного `scope.get_my_client`
(который требует бронь) — для чата клиент виден если у него есть либо
бронь в отеле партнёра, либо уже открытый чат-тред.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.models.models import (
    Booking,
    ChatMessage,
    ChatSenderKind,
    ChatThread,
    Client,
    Hotel,
    Room,
)
from app.schemas.chat import (
    HotelMini,
    MessagesPage,
    MessageView,
    SendMessageRequest,
    ThreadView,
)
from app.services import chat as chat_service

router = APIRouter()


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


async def _resolve_partner_thread(
    db: AsyncSession, ctx: AuthContext, client_id: int, hotel_id: int
) -> tuple[Hotel, Client, ChatThread]:
    """Получить (hotel, client, thread) с full permission + visibility check.

    Видимость клиента: бронь в отеле партнёра ИЛИ уже открытый чат-тред.
    """
    hotel = (
        await db.execute(select(Hotel).where(Hotel.id == hotel_id))
    ).scalar_one_or_none()
    if hotel is None:
        raise APIError(404, "not_found", "Hotel not found")

    perms = ctx.accessible_owners.get(hotel.owner_user_id)
    if perms is None:
        raise APIError(403, "forbidden", "Hotel not in your scope")
    if not perms.chat_with_clients:
        raise APIError(403, "permission_denied", "Missing permission: chat_with_clients")

    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise APIError(404, "not_found", "Client not found")
    if client.user_id is None:
        raise APIError(
            400, "bad_request", "Walk-in client has no chat (not linked to a user)"
        )

    has_booking = (
        await db.execute(
            select(Booking.id)
            .join(Room, Room.id == Booking.room_id)
            .where(Booking.client_id == client_id, Room.hotel_id == hotel_id)
            .limit(1)
        )
    ).scalar_one_or_none() is not None
    has_thread = (
        await db.execute(
            select(ChatThread.id).where(
                ChatThread.hotel_id == hotel_id,
                ChatThread.client_user_id == client.user_id,
            )
        )
    ).scalar_one_or_none() is not None
    if not (has_booking or has_thread):
        raise APIError(
            404,
            "not_found",
            "Client not visible to this hotel (no booking or chat thread)",
        )

    thread = await chat_service.get_or_create_thread(
        db, hotel_id=hotel_id, client_user_id=client.user_id
    )
    return hotel, client, thread


@router.get("/clients/{client_id}/chat", response_model=ThreadView)
async def get_chat_thread(
    client_id: int,
    hotel_id: int = Query(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
) -> ThreadView:
    hotel, _, thread = await _resolve_partner_thread(db, ctx, client_id, hotel_id)
    return ThreadView(
        id=thread.id,
        hotel=_hotel_to_mini(hotel),
        last_message_at=thread.last_message_at,
        unread_for_client=chat_service.is_unread_for(thread, ChatSenderKind.client),
        unread_for_hotel=chat_service.is_unread_for(thread, ChatSenderKind.hotel),
    )


@router.get("/clients/{client_id}/chat/messages", response_model=MessagesPage)
async def list_chat_messages(
    client_id: int,
    hotel_id: int = Query(...),
    cursor: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
) -> MessagesPage:
    _, _, thread = await _resolve_partner_thread(db, ctx, client_id, hotel_id)
    items, next_cursor = await chat_service.list_messages(
        db, thread.id, cursor, limit
    )
    return MessagesPage(
        items=[_msg_to_view(m) for m in items],
        next_cursor=next_cursor,
    )


@router.post(
    "/clients/{client_id}/chat/messages",
    response_model=MessageView,
    status_code=201,
)
async def send_chat_message(
    client_id: int,
    payload: SendMessageRequest,
    hotel_id: int = Query(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
) -> MessageView:
    hotel, _, thread = await _resolve_partner_thread(db, ctx, client_id, hotel_id)
    msg = await chat_service.append_message(
        db,
        thread=thread,
        sender_kind=ChatSenderKind.hotel,
        sender_user_id=ctx.user.id,
        body=payload.body,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
    )
    await audit(
        db,
        ctx,
        owner_user_id=hotel.owner_user_id,
        action="chat.message_sent",
        subject_type="chat_message",
        subject_id=msg.id,
        payload={
            "thread_id": thread.id,
            "hotel_id": hotel_id,
            "client_id": client_id,
            "preview": msg.body[:80],
        },
    )
    return _msg_to_view(msg)


@router.post("/clients/{client_id}/chat/read", status_code=204)
async def mark_chat_read(
    client_id: int,
    hotel_id: int = Query(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
) -> None:
    _, _, thread = await _resolve_partner_thread(db, ctx, client_id, hotel_id)
    await chat_service.mark_read(db, thread, ChatSenderKind.hotel)
