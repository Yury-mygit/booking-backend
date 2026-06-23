"""Admin /admin/bookings — list + cancel.

Cancel освобождает Availability rows (status=booked) для дат брони:
строки без price_override удаляются (возвращая default-state), строки
с override остаются с status=free. Payment'ы не трогаем — refund
делает админ через payment provider отдельно.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import get_db
from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Client,
    Hotel,
    Room,
)
from app.schemas.admin import AdminBookingView

from ._deps import admin_only

router = APIRouter()


@router.get("/bookings", response_model=list[AdminBookingView])
async def list_all_bookings(
    status_filter: BookingStatus | None = Query(default=None, alias="status"),
    hotel_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Booking, Room, Hotel, Client)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .join(Client, Client.id == Booking.client_id)
        .order_by(Booking.created_at.desc())
        .limit(500)
    )
    if status_filter is not None:
        stmt = stmt.where(Booking.status == status_filter)
    if hotel_id is not None:
        stmt = stmt.where(Hotel.id == hotel_id)
    rows = (await db.execute(stmt)).all()
    return [
        AdminBookingView(
            id=b.id,
            code=b.code,
            client_id=b.client_id,
            client_first_name=c.first_name,
            room_id=b.room_id,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            check_in=b.check_in,
            check_out=b.check_out,
            adults=b.adults,
            children=b.children,
            infants=b.infants,
            child_ages=b.child_ages,
            total_kgs=b.total_kgs,
            status=b.status,
            created_at=b.created_at,
        )
        for b, r, h, c in rows
    ]


@router.post("/bookings/{code}/cancel", response_model=AdminBookingView)
async def cancel_booking(
    code: str,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    booking = (
        await db.execute(select(Booking).where(Booking.code == code).with_for_update())
    ).scalar_one_or_none()
    if booking is None:
        raise APIError(404, "not_found", "Booking not found")
    if booking.status in (BookingStatus.cancelled, BookingStatus.refunded):
        raise APIError(409, "conflict", f"Booking already {booking.status.value}")

    # Free availability rows that we booked: rows with status=booked in range.
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

    # If there are paid payments, this would warrant a refund — we just flip the
    # booking status; payments table is not auto-touched (admin handles refund
    # via payment provider separately).
    booking.status = BookingStatus.cancelled
    hotel_id_for_pub = (
        await db.execute(select(Room.hotel_id).where(Room.id == booking.room_id))
    ).scalar_one()
    await db.commit()
    await pubsub.publish_refresh(hotel_id_for_pub)

    row = (
        await db.execute(
            select(Room, Hotel, Client)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .join(Client, Client.id == booking.client_id)
            .where(Room.id == booking.room_id)
        )
    ).first()
    _, hotel, client = row
    return AdminBookingView(
        id=booking.id,
        code=booking.code,
        client_id=booking.client_id,
        client_first_name=client.first_name,
        room_id=booking.room_id,
        hotel_id=hotel.id,
        hotel_name_ru=hotel.name_ru,
        check_in=booking.check_in,
        check_out=booking.check_out,
        adults=booking.adults,
        children=booking.children,
        infants=booking.infants,
        child_ages=booking.child_ages,
        total_kgs=booking.total_kgs,
        status=booking.status,
        created_at=booking.created_at,
    )
