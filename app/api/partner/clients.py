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
    """All clients who have at least one booking in any of my accessible
    owners' hotels (optionally scoped to one ?owner_id=)."""
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    from sqlalchemy import func as sa_func
    stmt = (
        select(
            Client,
            sa_func.count(Booking.id).label("cnt"),
            sa_func.max(Booking.check_in).label("last_date"),
        )
        .join(Booking, Booking.client_id == Client.id)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .where(Hotel.owner_user_id.in_(accessible_ids))
        .group_by(Client.id)
        .order_by(sa_func.max(Booking.created_at).desc())
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
    c = await scope.get_my_client(db, ctx, client_id)
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
    return ClientPartnerView.from_model(c, bookings_count=cnt or 0, last_booking_date=last)


@router.get("/clients/{client_id}/bookings", response_model=list[PartnerBookingView])
async def list_my_client_bookings(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
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
    has_perm = any(
        oid in ctx.accessible_owners and ctx.accessible_owners[oid].has("manage_bookings")
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


