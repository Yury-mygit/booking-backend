"""Client-side платежи через DevPay (mock PSP).

Endpoints:
- `POST /c/bookings/{code}/pay/init` — создаёт Payment(pending) через
  DevPay intents API, возвращает methods (devpay checkout_url + optional
  QR partner'а).
- `POST /c/payments/webhook/devpay` — принимает webhook от DevPay, апдейтит
  Payment+Booking → paid, публикует SSE. Slice 4 добавит HMAC-подпись
  + Idempotency-Key.

Booking имеет два независимых дименшна: `confirmed` (партнёр подтвердил)
и `paid` (клиент оплатил). Для постоплатных броней (walk-in) — paid
выставляется через `/p/bookings/{code}/mark-paid`.
"""
from __future__ import annotations

from datetime import datetime, timezone

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
    payment_id: str
    amount_kgs: int
    provider: str
    methods: list[dict]
    booking_code: str
    booking_status: BookingStatus


class WebhookRequest(BaseModel):
    intent_id: str
    status: str
    amount_kgs: int | None = None
    provider_ref: str | None = None


class WebhookAck(BaseModel):
    ok: bool
    status: str


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
        payment_id=str(result.payment_id),
        amount_kgs=result.amount_kgs,
        provider=result.provider,
        methods=result.methods,
        booking_code=booking.code,
        booking_status=booking.status,
    )


@router.post("/payments/webhook/devpay", response_model=WebhookAck)
async def devpay_webhook(
    body: WebhookRequest,
    db: AsyncSession = Depends(get_db),
) -> WebhookAck:
    """Webhook от DevPay. Slice 2 — без auth (доверяем docker-network).
    Slice 4 добавит HMAC-подпись + Idempotency-Key.
    """
    row = (
        await db.execute(
            select(Payment, Booking, Room)
            .join(Booking, Booking.id == Payment.booking_id)
            .join(Room, Room.id == Booking.room_id)
            .where(Payment.provider_ref == body.intent_id)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", f"payment for intent {body.intent_id} not found")
    payment, booking, room = row

    if payment.status == PaymentStatus.paid:
        return WebhookAck(ok=True, status="already_paid")

    if body.status != "paid":
        # Slice 3 разберёт declined/cancelled — пока молча ack без мутации.
        return WebhookAck(ok=True, status=f"ignored:{body.status}")

    payment.status = PaymentStatus.paid
    payment.paid_at = datetime.now(timezone.utc)
    if booking.status == BookingStatus.pending:
        booking.status = BookingStatus.paid
        booking.confirmed = True
    hotel_id_for_pub = room.hotel_id
    await db.commit()
    await pubsub.publish_refresh(hotel_id_for_pub)
    return WebhookAck(ok=True, status="paid")
