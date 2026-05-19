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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Booking, Payment, PaymentProvider, PaymentStatus


@dataclass
class PayInitResult:
    payment_id: uuid.UUID
    amount_kgs: int
    provider: str
    methods: list[dict]  # [{key, label}]


class MockProvider:
    """Single-step mock provider: init creates a pending Payment row; the
    frontend then calls /c/payments/{id}/mock-confirm to settle it instantly.
    No external integration, no webhook.
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
        return PayInitResult(
            payment_id=payment.id,
            amount_kgs=payment.amount_kgs,
            provider=self.key,
            methods=[{"key": "mock", "label_key": "pay.method.mock"}],
        )

    async def mock_confirm(self, db: AsyncSession, payment: Payment) -> None:
        """Idempotent — already-paid stays paid, no double-charge."""
        if payment.status == PaymentStatus.paid:
            return
        payment.status = PaymentStatus.paid
        payment.paid_at = datetime.now(timezone.utc)


# Singleton dependency. Switch to ELQR by changing this assignment.
provider = MockProvider()
