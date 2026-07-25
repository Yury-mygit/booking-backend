"""Partner bookings: incoming.

Подтверждение (confirm) и отмена (cancel) существующих клиентских
броней. Walk-in flow / postpay / mark-paid удалены в TBB-24 —
бронирование существует только оплаченным онлайн.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, exists, select
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
    ChatMessage,
    ChatMessageKind,
    ChatSubjectType,
    Client,
    Hotel,
    Room,
)
from app.schemas.partner import PartnerBookingView

router = APIRouter()  # prefix задан в partner/__init__.py


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
    # TBB-31: маркер «⚠ запрошена отмена» — EXISTS системки в чате брони.
    has_cancel_req = (
        exists()
        .where(
            ChatMessage.subject_type == ChatSubjectType.booking,
            ChatMessage.subject_id == Booking.id,
            ChatMessage.kind == ChatMessageKind.cancellation_request,
        )
        .correlate(Booking)
        .label("has_cancellation_request")
    )
    stmt = (
        select(Booking, Room, Hotel, Client, has_cancel_req)
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
    return [
        PartnerBookingView.from_model(b, r, h, c, has_cancellation_request=bool(hcr))
        for b, r, h, c, hcr in rows
    ]


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
