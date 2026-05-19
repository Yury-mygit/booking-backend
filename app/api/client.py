from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import get_db
from app.core.deps import AuthContext, require_role
from app.core.exceptions import APIError
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Client,
    Hotel,
    HotelStatus,
    Room,
    UserRole,
)
from app.schemas.bookings import BookingResponse, CreateBookingRequest
from app.utils import date_range_nights, gen_booking_code, get_or_create_client_for_user

router = APIRouter(prefix="/c", tags=["client"])


def _validate_dates(check_in: date, check_out: date) -> None:
    if check_out <= check_in:
        raise APIError(400, "bad_request", "check_out must be after check_in")
    today = date.today()
    if check_in < today:
        raise APIError(400, "bad_request", "check_in is in the past")


async def _build_response(db: AsyncSession, booking: Booking) -> BookingResponse:
    room_hotel = (
        await db.execute(
            select(Room, Hotel).join(Hotel, Hotel.id == Room.hotel_id).where(Room.id == booking.room_id)
        )
    ).first()
    if room_hotel is None:
        raise APIError(500, "internal", "Room/hotel missing")
    _, hotel = room_hotel
    return BookingResponse(
        id=booking.id,
        code=booking.code,
        room_id=booking.room_id,
        hotel_id=hotel.id,
        hotel_name_ru=hotel.name_ru,
        check_in=booking.check_in,
        check_out=booking.check_out,
        guests=booking.guests,
        total_kgs=booking.total_kgs,
        status=booking.status,
        postpay=booking.postpay,
        confirmed=booking.confirmed,
        created_at=booking.created_at,
    )


@router.post("/bookings", response_model=BookingResponse, status_code=201)
async def create_booking(
    payload: CreateBookingRequest,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> BookingResponse:
    _validate_dates(payload.check_in, payload.check_out)

    # Lock the room row.
    room = (
        await db.execute(select(Room).where(Room.id == payload.room_id).with_for_update())
    ).scalar_one_or_none()
    if room is None:
        raise APIError(404, "not_found", "Room not found")
    if room.capacity < payload.guests:
        raise APIError(400, "bad_request", "Too many guests for this room")

    # Hotel must be published.
    hotel_status = (
        await db.execute(select(Hotel.status).where(Hotel.id == room.hotel_id))
    ).scalar_one()
    if hotel_status != HotelStatus.published:
        raise APIError(404, "not_found", "Hotel not available")

    # Lock availability rows in range; verify none are blocked/booked.
    nights = list(date_range_nights(payload.check_in, payload.check_out))
    existing = (
        await db.execute(
            select(Availability)
            .where(
                Availability.room_id == payload.room_id,
                Availability.date >= payload.check_in,
                Availability.date < payload.check_out,
            )
            .with_for_update()
        )
    ).scalars().all()
    by_date: dict[date, Availability] = {a.date: a for a in existing}

    for d in nights:
        a = by_date.get(d)
        if a is not None and a.status in (AvailabilityStatus.blocked, AvailabilityStatus.booked):
            raise APIError(409, "conflict", f"Night {d.isoformat()} is not available")

    # Compute total: use price_override if present, else room.price_kgs.
    total = 0
    for d in nights:
        a = by_date.get(d)
        total += a.price_override if (a and a.price_override is not None) else room.price_kgs

    # Mark each night booked (upsert).
    for d in nights:
        stmt = (
            pg_insert(Availability)
            .values(room_id=payload.room_id, date=d, status=AvailabilityStatus.booked)
            .on_conflict_do_update(
                index_elements=["room_id", "date"],
                set_={"status": AvailabilityStatus.booked},
            )
        )
        await db.execute(stmt)

    # Generate unique code (retry on rare collision).
    for _ in range(5):
        code = gen_booking_code()
        clash = (
            await db.execute(select(Booking.id).where(Booking.code == code))
        ).scalar_one_or_none()
        if clash is None:
            break
    else:
        raise APIError(500, "internal", "Failed to generate booking code")

    client = await get_or_create_client_for_user(db, ctx.user)
    booking = Booking(
        code=code,
        client_id=client.id,
        room_id=payload.room_id,
        check_in=payload.check_in,
        check_out=payload.check_out,
        guests=payload.guests,
        total_kgs=total,
        status=BookingStatus.pending,
    )
    db.add(booking)
    hotel_id_for_pub = room.hotel_id  # capture before commit (avoid expired attrs).
    await db.commit()
    await db.refresh(booking)
    await pubsub.publish_refresh(hotel_id_for_pub)
    return await _build_response(db, booking)


@router.get("/bookings", response_model=list[BookingResponse])
async def list_my_bookings(
    hotel_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> list[BookingResponse]:
    stmt = (
        select(Booking)
        .join(Client, Client.id == Booking.client_id)
        .where(Client.user_id == ctx.user.id)
        .order_by(Booking.created_at.desc())
        .limit(100)
    )
    if hotel_id is not None:
        stmt = stmt.join(Room, Room.id == Booking.room_id).where(Room.hotel_id == hotel_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [await _build_response(db, b) for b in rows]


@router.get("/bookings/{code}", response_model=BookingResponse)
async def get_my_booking(
    code: str,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> BookingResponse:
    booking = (
        await db.execute(
            select(Booking)
            .join(Client, Client.id == Booking.client_id)
            .where(Booking.code == code, Client.user_id == ctx.user.id)
        )
    ).scalar_one_or_none()
    if booking is None:
        raise APIError(404, "not_found", "Booking not found")
    return await _build_response(db, booking)
