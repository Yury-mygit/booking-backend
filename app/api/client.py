"""Client-side booking endpoints (/c/bookings*, любой залогиненный юзер).

POST /c/bookings — создание брони гостем (auto-cancel через 15 мин, если
не оплачена/не подтверждена; см. `core/autocancel.py`).
GET  /c/bookings, /c/bookings/{code} — мои брони (фильтр по client_id из
сессии).

Платежи живут в `payments.py` (тот же префикс /c, отдельный модуль).
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
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
    RoomStatus,
    UserRole,
)
from app.schemas.bookings import (
    BookingMediaResponse,
    BookingResponse,
    CancellationRequestBody,
    CancellationRequestResponse,
    CreateBookingRequest,
)
from app.services import cancellation as cancellation_service
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
    photos = hotel.photos or []
    return BookingResponse(
        id=booking.id,
        code=booking.code,
        room_id=booking.room_id,
        hotel_id=hotel.id,
        hotel_name_ru=hotel.name_ru,
        hotel_photo=photos[0] if photos else None,
        check_in=booking.check_in,
        check_out=booking.check_out,
        adults=booking.adults,
        children=booking.children,
        infants=booking.infants,
        child_ages=booking.child_ages,
        total_kgs=booking.total_kgs,
        status=booking.status,
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
    if room.status != RoomStatus.published:
        raise APIError(404, "not_found", "Room not found")
    if room.capacity < payload.adults + payload.children:
        raise APIError(400, "bad_request", "Too many guests for this room")

    # Hotel must be published (нужен полный row — с него snapshot'ятся правила).
    hotel = (
        await db.execute(select(Hotel).where(Hotel.id == room.hotel_id))
    ).scalar_one()
    if hotel.status != HotelStatus.published:
        raise APIError(404, "not_found", "Hotel not available")

    # TBB-63: enforce min_stay_nights. Live-check против hotel row (снапшот
    # появляется В МОМЕНТЕ create, использование snapshot тут — kostyl).
    stay_nights = (payload.check_out - payload.check_in).days
    if stay_nights < hotel.min_stay_nights:
        raise APIError(
            400,
            "stay_too_short",
            f"Минимальный срок бронирования — {hotel.min_stay_nights} ноч.",
            detail={"min_stay_nights": hotel.min_stay_nights},
        )

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
        adults=payload.adults,
        children=payload.children,
        infants=payload.infants,
        child_ages=payload.child_ages,
        total_kgs=total,
        status=BookingStatus.pending,
        # TBB-61: immutable snapshot правил hotel на момент создания.
        # Партнёр не может ретроактивно ужесточить условия уже созданной брони.
        snapshot_min_stay_nights=hotel.min_stay_nights,
        snapshot_booking_mode=hotel.booking_mode,
        snapshot_cancel_policy=hotel.cancel_policy,
        snapshot_cancel_days_threshold=hotel.cancel_days_threshold,
        snapshot_cancel_penalty_pct=hotel.cancel_penalty_pct,
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


@router.get("/bookings/{code}/media", response_model=BookingMediaResponse)
async def get_my_booking_media(
    code: str,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> BookingMediaResponse:
    row = (
        await db.execute(
            select(Booking, Room, Hotel)
            .join(Client, Client.id == Booking.client_id)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Booking.code == code, Client.user_id == ctx.user.id)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Booking not found")
    _, room, hotel = row
    return BookingMediaResponse(
        hotel_photos=list(hotel.photos or []),
        room_photos=list(room.photos or []),
    )


@router.post("/bookings/{code}/cancel", response_model=BookingResponse)
async def cancel_my_booking(
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
    if booking.status in (BookingStatus.cancelled, BookingStatus.refunded):
        raise APIError(409, "conflict", f"Booking is already {booking.status.value}")

    avail_rows = (
        (
            await db.execute(
                select(Availability)
                .where(
                    Availability.room_id == booking.room_id,
                    Availability.date >= booking.check_in,
                    Availability.date < booking.check_out,
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

    booking.status = BookingStatus.cancelled
    room_hotel = (
        await db.execute(
            select(Room).where(Room.id == booking.room_id)
        )
    ).scalar_one()
    hotel_id_for_pub = room_hotel.hotel_id
    await db.commit()
    await db.refresh(booking)
    await pubsub.publish_refresh(hotel_id_for_pub)
    return await _build_response(db, booking)


@router.post(
    "/bookings/{code}/cancellation-request",
    response_model=CancellationRequestResponse,
)
async def request_booking_cancellation(
    code: str,
    body: CancellationRequestBody,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> CancellationRequestResponse:
    """TBB-31: клиент запрашивает отмену подтверждённого бронирования.

    Не меняет booking.status — бронь остаётся confirmed. Создаёт
    системное сообщение в чате брони; партнёр обрабатывает вручную.
    """
    booking = (
        await db.execute(
            select(Booking)
            .join(Client, Client.id == Booking.client_id)
            .where(Booking.code == code, Client.user_id == ctx.user.id)
        )
    ).scalar_one_or_none()
    if booking is None:
        raise APIError(404, "not_found", "Booking not found")
    if not booking.confirmed:
        raise APIError(
            409,
            "not_confirmed",
            "Only confirmed bookings can be cancelled through this flow",
        )
    if booking.status in (BookingStatus.cancelled, BookingStatus.refunded):
        raise APIError(409, "conflict", f"Booking is already {booking.status.value}")

    msg = await cancellation_service.create_cancellation_request(
        db,
        booking=booking,
        reasons=[r.value for r in body.reasons],
        note=body.note,
        client_user=ctx.user,
    )
    return CancellationRequestResponse(
        booking_code=booking.code, requested_at=msg.created_at
    )
