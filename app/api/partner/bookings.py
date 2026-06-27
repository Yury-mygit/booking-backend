"""Partner bookings: incoming + walk-in.

Incoming: подтверждение/отметка оплаты/отмена/postpay-флаг.
Walk-in: партнёрское создание брони (postpay=true, confirmed=true).
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.core.audit import audit
from app.services import scope
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Client,
    Hotel,
    Room,
)
from app.schemas.partner import (
    PartnerBookingPostpaySet,
    PartnerBookingView,
    WalkinBookingCreate,
)
from app.utils import (
    date_range_nights,
    gen_booking_code,
    normalize_email,
    normalize_phone,
)

router = APIRouter()  # prefix задан в partner/__init__.py


# ─── Incoming bookings ─────────────────────────────────────────────────────

@router.get("/bookings", response_model=list[PartnerBookingView])
async def list_incoming_bookings(
    status_filter: BookingStatus | None = Query(default=None, alias="status"),
    hotel_id: int | None = Query(default=None),
    owner_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    stmt = (
        select(Booking, Room, Hotel, Client)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .join(Client, Client.id == Booking.client_id)
        .where(Hotel.owner_user_id.in_(accessible_ids))
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        stmt = stmt.where(Booking.status == status_filter)
    if hotel_id is not None:
        stmt = stmt.where(Hotel.id == hotel_id)

    rows = (await db.execute(stmt)).all()
    return [PartnerBookingView.from_model(b, r, h, c) for b, r, h, c in rows]


@router.post("/bookings/{code}/confirm", response_model=PartnerBookingView)
async def confirm_booking(
    code: str,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """Partner guarantee to accept the guest. Sets confirmed=true; does not
    touch payment status. Online-paid bookings auto-confirm via /c/payments."""
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status != BookingStatus.pending:
        raise APIError(409, "conflict", f"Booking is {b.status.value}, cannot confirm")
    if b.confirmed:
        raise APIError(409, "conflict", "Booking is already confirmed")
    b.confirmed = True
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.confirm",
        subject_type="booking",
        subject_id=b.id,
        hotel_id=hotel_id_for_pub,
        payload={"code": b.code, "hotel_id": hotel_id_for_pub},
    )
    return PartnerBookingView.from_model(b, r, h, c)


@router.post("/bookings/{code}/mark-paid", response_model=PartnerBookingView)
async def mark_paid(
    code: str,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """Postpay flow: record physical cash receipt → status=paid + confirmed=true.
    Only for postpay bookings; online ones become paid automatically through
    /c/payments and don't need this."""
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status != BookingStatus.pending:
        raise APIError(409, "conflict", f"Booking is {b.status.value}, cannot mark paid")
    if not b.postpay:
        raise APIError(
            400,
            "bad_request",
            "Only postpay bookings accept manual mark-paid; online bookings settle via /c/payments",
        )
    b.status = BookingStatus.paid
    b.confirmed = True  # paid implies confirmed
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.mark_paid",
        subject_type="booking",
        subject_id=b.id,
        hotel_id=hotel_id_for_pub,
        payload={"code": b.code, "hotel_id": hotel_id_for_pub},
    )
    return PartnerBookingView.from_model(b, r, h, c)


@router.post("/bookings/{code}/postpay", response_model=PartnerBookingView)
async def set_postpay(
    code: str,
    payload: PartnerBookingPostpaySet,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """Toggle postpay flag on a booking. Postpay = «оплата у стойки», 24h
    auto-cancel skips it and partner confirms manually after physical payment."""
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status not in (BookingStatus.pending, BookingStatus.paid):
        raise APIError(409, "conflict", f"Cannot change postpay on {b.status.value} booking")
    b.postpay = payload.postpay
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.postpay",
        subject_type="booking",
        subject_id=b.id,
        hotel_id=hotel_id_for_pub,
        payload={"code": b.code, "postpay": b.postpay},
    )
    return PartnerBookingView.from_model(b, r, h, c)


@router.post("/bookings/{code}/cancel", response_model=PartnerBookingView)
async def cancel_booking(
    code: str,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status in (BookingStatus.cancelled, BookingStatus.refunded):
        raise APIError(409, "conflict", f"Booking is already {b.status.value}")

    avail_rows = (
        (
            await db.execute(
                select(Availability)
                .where(
                    Availability.room_id == b.room_id,
                    Availability.date >= b.check_in,
                    Availability.date < b.check_out,
                    Availability.status == AvailabilityStatus.booked,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for a in avail_rows:
        if a.price_override is None:
            await db.execute(
                delete(Availability).where(
                    Availability.room_id == a.room_id, Availability.date == a.date
                )
            )
        else:
            a.status = AvailabilityStatus.free

    b.status = BookingStatus.cancelled
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.cancel",
        subject_type="booking",
        subject_id=b.id,
        hotel_id=hotel_id_for_pub,
        payload={"code": b.code, "hotel_id": hotel_id_for_pub},
    )
    return PartnerBookingView.from_model(b, r, h, c)


# ─── Walk-in bookings ─────────────────────────────────────────────────────

async def _find_or_create_client_for_walkin(
    db: AsyncSession, payload: WalkinBookingCreate
) -> Client:
    """Dedup walk-in clients by normalized phone / email; create otherwise."""
    norm_phone = normalize_phone(payload.phone)
    norm_email = normalize_email(payload.email)
    existing: Client | None = None
    if norm_phone:
        existing = (
            await db.execute(select(Client).where(Client.phone == norm_phone))
        ).scalar_one_or_none()
    if existing is None and norm_email:
        existing = (
            await db.execute(select(Client).where(Client.email == norm_email))
        ).scalar_one_or_none()
    if existing is not None:
        return existing
    c = Client(
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone=norm_phone,
        email=norm_email,
        doc_kind=payload.doc_kind,
        doc_number=payload.doc_number,
    )
    db.add(c)
    await db.flush()
    return c


@router.post("/walkin-bookings", response_model=PartnerBookingView, status_code=201)
async def create_walkin_booking(
    payload: WalkinBookingCreate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    if payload.check_out <= payload.check_in:
        raise APIError(400, "bad_request", "check_out must be after check_in")
    today = date.today()
    if payload.check_in < today:
        raise APIError(400, "bad_request", "check_in is in the past")

    # Verify room belongs to an accessible owner's hotel + lock it.
    accessible_ids = list(ctx.accessible_owners.keys())
    row = (
        await db.execute(
            select(Room, Hotel)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Room.id == payload.room_id, Hotel.owner_user_id.in_(accessible_ids))
            .with_for_update(of=Room)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Room not found")
    room, hotel = row
    if not ctx.accessible_owners[hotel.owner_user_id].can(hotel.id, "manage_bookings"):
        raise APIError(403, "permission_denied", "Missing permission: manage_bookings")
    if room.capacity < payload.adults + payload.children:
        raise APIError(400, "bad_request", "Too many guests for this room")

    # Lock availability rows in range; verify nothing is blocked/booked.
    nights = list(date_range_nights(payload.check_in, payload.check_out))
    existing_av = (
        await db.execute(
            select(Availability)
            .where(
                Availability.room_id == room.id,
                Availability.date >= payload.check_in,
                Availability.date < payload.check_out,
            )
            .with_for_update()
        )
    ).scalars().all()
    by_date = {a.date: a for a in existing_av}
    for d in nights:
        a = by_date.get(d)
        if a is not None and a.status in (AvailabilityStatus.blocked, AvailabilityStatus.booked):
            raise APIError(409, "conflict", f"Night {d.isoformat()} is not available")

    total = 0
    for d in nights:
        a = by_date.get(d)
        total += a.price_override if (a and a.price_override is not None) else room.price_kgs

    for d in nights:
        stmt = (
            pg_insert(Availability)
            .values(room_id=room.id, date=d, status=AvailabilityStatus.booked)
            .on_conflict_do_update(
                index_elements=["room_id", "date"],
                set_={"status": AvailabilityStatus.booked},
            )
        )
        await db.execute(stmt)

    client = await _find_or_create_client_for_walkin(db, payload)

    for _ in range(5):
        code = gen_booking_code()
        clash = (
            await db.execute(select(Booking.id).where(Booking.code == code))
        ).scalar_one_or_none()
        if clash is None:
            break
    else:
        raise APIError(500, "internal", "Failed to generate booking code")

    booking = Booking(
        code=code,
        client_id=client.id,
        room_id=room.id,
        check_in=payload.check_in,
        check_out=payload.check_out,
        adults=payload.adults,
        children=payload.children,
        infants=payload.infants,
        child_ages=payload.child_ages,
        total_kgs=total,
        status=BookingStatus.pending,
        postpay=True,    # walk-in = физ. оплата у стойки; не подпадает под 24h auto-cancel
        confirmed=True,  # партнёр сам её создал — гарантия приёма автоматически
    )
    db.add(booking)
    hotel_id_for_pub = hotel.id
    owner_id_snap = hotel.owner_user_id
    await db.commit()
    await db.refresh(booking)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="walkin.create",
        subject_type="booking",
        subject_id=booking.id,
        hotel_id=hotel_id_for_pub,
        payload={
            "code": booking.code,
            "hotel_id": hotel_id_for_pub,
            "room_id": room.id,
            "check_in": str(booking.check_in),
            "check_out": str(booking.check_out),
            "total_kgs": booking.total_kgs,
        },
    )
    return PartnerBookingView.from_model(booking, room, hotel, client)


