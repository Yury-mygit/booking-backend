"""Payment providers.

Interface is intentionally tiny: init creates the provider-side intent (returning
data the frontend needs to render a payment form), mock_confirm advances a mock
payment to paid synchronously. ELQRProvider (later) will replace mock_confirm
with a real webhook flow.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Booking,
    Hotel,
    Payment,
    PaymentProvider,
    PaymentStatus,
    Room,
    User,
)


@dataclass
class PayInitResult:
    payment_id: uuid.UUID
    amount_kgs: int
    provider: str
    methods: list[dict]  # [{key, label_key, ...method-specific}]


class MockProvider:
    """Single-step mock provider: init creates a pending Payment row; the
    frontend then calls /c/payments/{id}/mock-confirm to settle it instantly.
    No external integration, no webhook.

    В список methods опционально добавляется 'qr' — если у owner'а отеля
    заведён User.qr_image_url. Клиент видит картинку QR + кнопку
    «Оплатить», которая на фронте декодирует URL из QR и открывает его
    (TBB-29). Booking при этом остаётся pending; партнёр подтверждает
    оплату вручную через /p/bookings/{code}/mark-paid.
    """

    key = "mock"

    async def init(self, db: AsyncSession, booking: Booking) -> PayInitResult:
        payment = Payment(
            booking_id=booking.id,
            provider=PaymentProvider.mock,
            amount_kgs=booking.total_kgs,
            status=PaymentStatus.pending,
        )
        db.add(payment)
        await db.flush()

        methods: list[dict] = [{"key": "mock", "label_key": "pay.method.mock"}]
        owner_qr_url = (
            await db.execute(
                select(User.qr_image_url)
                .join(Hotel, Hotel.owner_user_id == User.id)
                .join(Room, Room.hotel_id == Hotel.id)
                .where(Room.id == booking.room_id)
            )
        ).scalar_one_or_none()
        if owner_qr_url:
            methods.append({
                "key": "qr",
                "label_key": "pay.method.qr",
                "qr_image_url": owner_qr_url,
            })

        return PayInitResult(
            payment_id=payment.id,
            amount_kgs=payment.amount_kgs,
            provider=self.key,
            methods=methods,
        )

    async def mock_confirm(self, db: AsyncSession, payment: Payment) -> None:
        """Idempotent — already-paid stays paid, no double-charge."""
        if payment.status == PaymentStatus.paid:
            return
        payment.status = PaymentStatus.paid
        payment.paid_at = datetime.now(timezone.utc)


# Singleton dependency. Switch to ELQR by changing this assignment.
provider = MockProvider()
