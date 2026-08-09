"""Client-side платежи через DevPay (mock PSP) — DP-3/4/5.

Endpoints:
- `POST /c/bookings/{code}/pay/init` — создаёт Payment(pending) через
  DevPay intents API, возвращает methods (devpay checkout_url + optional
  QR partner'а).
- `POST /c/payments/webhook/devpay` — принимает webhook от DevPay c
  HMAC-подписью + Idempotency-Key (DP-5). Апдейтит Payment+Booking,
  публикует SSE, кеширует response по Idempotency-Key.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, require_role
from app.core.exceptions import APIError
from app.core.payments import provider as payment_provider
from app.core.webhook_security import verify_signature, verify_timestamp
from app.models.models import (
    Booking,
    BookingStatus,
    Client,
    Payment,
    PaymentStatus,
    Room,
    UserRole,
    WebhookIdempotency,
)


router = APIRouter(prefix="/c", tags=["payments"])


IDEMPOTENCY_TTL = timedelta(days=7)
DEVPAY_ENDPOINT = "devpay_webhook"


class PayInitResponse(BaseModel):
    payment_id: str
    amount_kgs: int
    provider: str
    methods: list[dict]
    booking_code: str
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
        payment_id=str(result.payment_id),
        amount_kgs=result.amount_kgs,
        provider=result.provider,
        methods=result.methods,
        booking_code=booking.code,
        booking_status=booking.status,
    )


async def _cached_response(
    db: AsyncSession, key: str
) -> dict | None:
    row = (
        await db.execute(
            select(WebhookIdempotency).where(WebhookIdempotency.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at < datetime.now(timezone.utc):
        return None
    return row.response_body


async def _store_cache(
    db: AsyncSession, key: str, response_body: dict
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        WebhookIdempotency(
            key=key,
            endpoint=DEVPAY_ENDPOINT,
            response_body=response_body,
            http_status=200,
            created_at=now,
            expires_at=now + IDEMPOTENCY_TTL,
        )
    )


@router.post("/payments/webhook/devpay")
async def devpay_webhook(
    request: Request,
    x_devpay_signature: str | None = Header(default=None),
    x_devpay_timestamp: str | None = Header(default=None),
    x_devpay_idempotency_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw = await request.body()

    # Security
    if not verify_signature(raw, x_devpay_signature, settings.devpay_merchant_secret):
        raise APIError(401, "bad_signature", "Invalid HMAC signature")
    if not verify_timestamp(x_devpay_timestamp):
        raise APIError(401, "stale_timestamp", "Timestamp out of range")
    if not x_devpay_idempotency_key:
        raise APIError(400, "missing_idempotency_key", "X-Devpay-Idempotency-Key required")

    # Idempotency cache
    key = f"{DEVPAY_ENDPOINT}:{x_devpay_idempotency_key}"
    cached = await _cached_response(db, key)
    if cached is not None:
        return cached

    try:
        body = json.loads(raw.decode())
    except Exception:
        raise APIError(400, "bad_json", "Invalid JSON body")

    intent_id = body.get("intent_id")
    status_str = body.get("status")
    reason = body.get("reason")
    if not intent_id or not status_str:
        raise APIError(400, "bad_payload", "intent_id + status required")

    row = (
        await db.execute(
            select(Payment, Booking, Room)
            .join(Booking, Booking.id == Payment.booking_id)
            .join(Room, Room.id == Booking.room_id)
            .where(Payment.provider_ref == intent_id)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", f"payment for intent {intent_id} not found")
    payment, booking, room = row

    if payment.status in (PaymentStatus.paid, PaymentStatus.failed, PaymentStatus.cancelled):
        response = {"ok": True, "status": f"already_{payment.status.value}"}
        await _store_cache(db, key, response)
        await db.commit()
        return response

    hotel_id_for_pub = room.hotel_id
    if status_str == "paid":
        payment.status = PaymentStatus.paid
        payment.paid_at = datetime.now(timezone.utc)
        if booking.status == BookingStatus.pending:
            booking.status = BookingStatus.paid
            booking.confirmed = True
        response = {"ok": True, "status": "paid"}
    elif status_str == "declined":
        payment.status = PaymentStatus.failed
        response = {"ok": True, "status": "declined", "reason": reason}
    elif status_str == "cancelled":
        payment.status = PaymentStatus.cancelled
        response = {"ok": True, "status": "cancelled"}
    else:
        raise APIError(400, "bad_status", f"Unknown webhook status: {status_str}")

    await _store_cache(db, key, response)
    await db.commit()
    await pubsub.publish_refresh(hotel_id_for_pub)
    return response
