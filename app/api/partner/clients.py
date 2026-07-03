"""Partner clients: список собственных клиентов + lookup по phone/email."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.core.audit import audit
from app.services import scope
from app.models.models import (
    Booking,
    ChatThread,
    Client,
    Hotel,
    Room,
)
from app.schemas.partner import (
    ClientLookup,
    ClientPartnerView,
    ClientUpdate,
    PartnerBookingView,
)
from app.schemas.partner.clients import ClientChatHotel
from app.utils import (
    normalize_email,
    normalize_phone,
)

router = APIRouter()  # prefix задан в partner/__init__.py


# ─── /p/clients ────────────────────────────────────────────────────────────

@router.get("/clients", response_model=list[ClientPartnerView])
async def list_my_clients(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """Clients with either a booking OR an open chat thread in my accessible
    owners' hotels (optionally scoped to one ?owner_id=). Prospect'ы (только
    chat, без броней) идут вместе с booked, флаг `is_prospect` в response.
    Sort — unified `last_activity_at = max(last_booking_at, last_message_at)` DESC.
    """
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    if not accessible_ids:
        return []
    from sqlalchemy import func as sa_func

    booked_subq = (
        select(
            Client.id.label("client_id"),
            sa_func.count(Booking.id).label("cnt"),
            sa_func.max(Booking.check_in).label("last_date"),
            sa_func.max(Booking.created_at).label("booking_activity"),
        )
        .join(Booking, Booking.client_id == Client.id)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .where(Hotel.owner_user_id.in_(accessible_ids))
        .group_by(Client.id)
        .subquery()
    )

    chats_subq = (
        select(
            Client.id.label("client_id"),
            sa_func.max(ChatThread.last_message_at).label("chat_activity"),
        )
        .join(ChatThread, ChatThread.client_user_id == Client.user_id)
        .join(Hotel, Hotel.id == ChatThread.hotel_id)
        .where(
            Hotel.owner_user_id.in_(accessible_ids),
            ChatThread.last_message_at.is_not(None),
        )
        .group_by(Client.id)
        .subquery()
    )

    activity = sa_func.greatest(
        booked_subq.c.booking_activity, chats_subq.c.chat_activity
    )
    stmt = (
        select(
            Client,
            sa_func.coalesce(booked_subq.c.cnt, 0).label("cnt"),
            booked_subq.c.last_date.label("last_date"),
        )
        .outerjoin(booked_subq, booked_subq.c.client_id == Client.id)
        .outerjoin(chats_subq, chats_subq.c.client_id == Client.id)
        .where(
            (booked_subq.c.cnt.is_not(None))
            | (chats_subq.c.chat_activity.is_not(None))
        )
        .order_by(activity.desc().nullslast())
        .limit(500)
    )
    rows = (await db.execute(stmt)).all()
    unread = await _unread_chat_client_ids(db, accessible_ids)
    return [
        ClientPartnerView.from_model(
            c,
            bookings_count=cnt,
            last_booking_date=last,
            has_unread_chat=(c.id in unread),
            is_prospect=(cnt == 0),
        )
        for (c, cnt, last) in rows
    ]


async def _unread_chat_client_ids(
    db: AsyncSession, accessible_owner_ids: list[int]
) -> set[int]:
    """Возвращает set client.id у которых есть тред с непрочитанным со
    стороны отеля сообщением, в пределах accessible_owners.

    «Непрочитано» = `last_message_at > hotel_last_read_at` (или read=NULL).
    """
    if not accessible_owner_ids:
        return set()
    stmt = (
        select(Client.id)
        .join(ChatThread, ChatThread.client_user_id == Client.user_id)
        .join(Hotel, Hotel.id == ChatThread.hotel_id)
        .where(
            Hotel.owner_user_id.in_(accessible_owner_ids),
            ChatThread.last_message_at.is_not(None),
            (
                ChatThread.hotel_last_read_at.is_(None)
                | (ChatThread.last_message_at > ChatThread.hotel_last_read_at)
            ),
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars().all())


@router.post("/clients/lookup", response_model=ClientPartnerView | None)
async def lookup_client(
    payload: ClientLookup,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """For the walk-in form: find existing client by phone or email so the
    partner can pre-fill. Returns the global record (scope-agnostic). Returns
    null if nothing matched."""
    norm_phone = normalize_phone(payload.phone)
    norm_email = normalize_email(payload.email)
    if not norm_phone and not norm_email:
        return None
    c: Client | None = None
    if norm_phone:
        c = (await db.execute(select(Client).where(Client.phone == norm_phone))).scalar_one_or_none()
    if c is None and norm_email:
        c = (await db.execute(select(Client).where(Client.email == norm_email))).scalar_one_or_none()
    if c is None:
        return None
    return ClientPartnerView.from_model(c, bookings_count=0, last_booking_date=None)


@router.get("/clients/{client_id}", response_model=ClientPartnerView)
async def get_my_client(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id, include_chat_only=True)
    accessible_ids = list(ctx.accessible_owners.keys())
    from sqlalchemy import func as sa_func
    cnt, last = (
        await db.execute(
            select(sa_func.count(Booking.id), sa_func.max(Booking.check_in))
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Booking.client_id == c.id, Hotel.owner_user_id.in_(accessible_ids))
        )
    ).one()
    cnt = cnt or 0
    # Hotels в моих owners, где у клиента есть открытый chat_thread, но нет
    # bookings — surface для prospect-thread'ов в client_edit_chat.
    chat_hotels: list[ClientChatHotel] = []
    if c.user_id is not None:
        booked_hotel_ids_subq = (
            select(Hotel.id)
            .join(Room, Room.hotel_id == Hotel.id)
            .join(Booking, Booking.room_id == Room.id)
            .where(
                Booking.client_id == c.id,
                Hotel.owner_user_id.in_(accessible_ids),
            )
        )
        rows = (
            await db.execute(
                select(Hotel.id, Hotel.name_ru, Hotel.owner_user_id)
                .join(ChatThread, ChatThread.hotel_id == Hotel.id)
                .where(
                    ChatThread.client_user_id == c.user_id,
                    Hotel.owner_user_id.in_(accessible_ids),
                    Hotel.id.notin_(booked_hotel_ids_subq),
                )
                .distinct()
            )
        ).all()
        chat_hotels = [
            ClientChatHotel(id=h_id, name_ru=name, owner_user_id=owner)
            for (h_id, name, owner) in rows
        ]
    return ClientPartnerView.from_model(
        c,
        bookings_count=cnt,
        last_booking_date=last,
        is_prospect=(cnt == 0),
        chat_hotels=chat_hotels,
    )


@router.get("/clients/{client_id}/bookings", response_model=list[PartnerBookingView])
async def list_my_client_bookings(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id, include_chat_only=True)
    accessible_ids = list(ctx.accessible_owners.keys())
    rows = (
        await db.execute(
            select(Booking, Room, Hotel)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Booking.client_id == c.id, Hotel.owner_user_id.in_(accessible_ids))
            .order_by(Booking.created_at.desc())
        )
    ).all()
    return [PartnerBookingView.from_model(b, r, h, c) for (b, r, h) in rows]


@router.put("/clients/{client_id}", response_model=ClientPartnerView)
async def update_my_client(
    client_id: int,
    payload: ClientUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
    # Allow edit if user has manage_bookings on ANY accessible owner where the
    # client has bookings. Client records are global (one row), so this is the
    # cleanest gate that doesn't require per-owner forking.
    owner_ids_with_bookings = set(
        (
            await db.execute(
                select(Hotel.owner_user_id)
                .join(Room, Room.hotel_id == Hotel.id)
                .join(Booking, Booking.room_id == Room.id)
                .where(Booking.client_id == c.id)
                .distinct()
            )
        ).scalars()
    )
    # Cross-owner client edit: разрешено, если user имеет manage_bookings
    # хотя бы на одного owner'а, где у клиента есть бронь (any_hotel —
    # coarse view, точное per-hotel gating уже было сделано на момент
    # самой брони).
    has_perm = any(
        oid in ctx.accessible_owners and ctx.accessible_owners[oid].any_hotel("manage_bookings")
        for oid in owner_ids_with_bookings
    )
    if not has_perm:
        raise APIError(403, "permission_denied", "Missing permission: manage_bookings")
    data = payload.model_dump(exclude_unset=True)
    if "phone" in data:
        data["phone"] = normalize_phone(data["phone"])
    if "email" in data:
        data["email"] = normalize_email(data["email"])
    for k, v in data.items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    await audit(
        db, ctx,
        owner_user_id=next(iter(owner_ids_with_bookings & set(ctx.accessible_owners.keys()))),
        action="client.update",
        subject_type="client",
        subject_id=c.id,
        payload=data,
    )
    return ClientPartnerView.from_model(c, bookings_count=0, last_booking_date=None)


