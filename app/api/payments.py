"""Client-side платежи (/c/bookings/{code}/pay/init,
/c/payments/{id}/mock-confirm).

Mock-провайдер (см. `core/payments.py`): `pay/init` создаёт Payment в
PENDING, фронт показывает QR; `mock-confirm` переводит Payment+Booking в
PAID. Реальный провайдер заменит mock через тот же интерфейс
`payment_provider`.

Booking имеет два независимых дименшна: `confirmed` (партнёр подтвердил)
и `paid` (клиент оплатил). Для постоплатных броней (walk-in) — paid
выставляется через `/p/bookings/{code}/mark-paid`.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import get_db
from app.core.deps import AuthContext, require_role
from app.core.exceptions import APIError
from app.core.payments import provider as payment_provider
from app.models.models import (
    Booking,
    BookingStatus,
    Client,
    Payment,
    PaymentStatus,
    Room,
    UserRole,
)


router = APIRouter(prefix="/c", tags=["payments"])


class PayInitResponse(BaseModel):
    payment_id: uuid.UUID
    amount_kgs: int
    provider: str
    methods: list[dict]
    booking_code: str
    booking_status: BookingStatus


class PaymentView(BaseModel):
    id: uuid.UUID
    booking_code: str
    amount_kgs: int
    status: PaymentStatus
    provider: str
    booking_status: BookingStatus


async def _get_my_booking(db: AsyncSession, ctx: AuthContext, code: str) -> Booking:
    booking = (
        await db.execute(
            select(Booking)
            .join(Client, Client.id == Booking.client_id)
            .where(Booking.code == code, Client.user_id == ctx.user.id)
        )
    ).scalar_one_or_none()
    if booking is None:
        raise APIError(404, "not_found", "Booking not found")
    return booking


@router.post("/bookings/{code}/pay/init", response_model=PayInitResponse)
async def pay_init(
    code: str,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> PayInitResponse:
    booking = await _get_my_booking(db, ctx, code)
    if booking.status != BookingStatus.pending:
        raise APIError(409, "conflict", f"Booking is {booking.status.value}, not pending")

    result = await payment_provider.init(db, booking)
    await db.commit()
    return PayInitResponse(
        payment_id=result.payment_id,
        amount_kgs=result.amount_kgs,
        provider=result.provider,
        methods=result.methods,
        booking_code=booking.code,
        booking_status=booking.status,
    )


@router.post("/payments/{payment_id}/mock-confirm", response_model=PaymentView)
async def mock_confirm(
    payment_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(UserRole.client)),
    db: AsyncSession = Depends(get_db),
) -> PaymentView:
    row = (
        await db.execute(
            select(Payment, Booking, Client, Room)
            .join(Booking, Booking.id == Payment.booking_id)
            .join(Client, Client.id == Booking.client_id)
            .join(Room, Room.id == Booking.room_id)
            .where(Payment.id == payment_id, Client.user_id == ctx.user.id)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Payment not found")
    payment, booking, _, room = row

    await payment_provider.mock_confirm(db, payment)
    if booking.status == BookingStatus.pending and payment.status == PaymentStatus.paid:
        booking.status = BookingStatus.paid
        booking.confirmed = True  # paid implies confirmed
    hotel_id_for_pub = room.hotel_id
    await db.commit()
    await db.refresh(payment)
    await db.refresh(booking)
    await pubsub.publish_refresh(hotel_id_for_pub)
    return PaymentView(
        id=payment.id,
        booking_code=booking.code,
        amount_kgs=payment.amount_kgs,
        status=payment.status,
        provider=payment.provider.value,
        booking_status=booking.status,
    )
