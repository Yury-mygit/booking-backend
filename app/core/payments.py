"""Payment providers.

Booking → PSP через тонкий интерфейс: `init` создаёт intent на стороне PSP,
`handle_webhook` вызывается когда PSP присылает webhook о финальном статусе.

Slice 2 (DP-3): единственный provider — DevPay (sandbox PSP). Реальный ELQR
подключается через новый класс с тем же интерфейсом.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
    methods: list[dict[str, Any]]  # [{key, label_key, ...method-specific}]


class DevPayProvider:
    """Sandbox PSP через HTTP intents API.

    init:
      1. POST DEVPAY_INTERNAL_URL/api/v1/intents с Bearer, получает
         {intent_id, checkout_url}.
      2. Сохраняет Payment(pending, provider=devpay, provider_ref=intent_id).
      3. Возвращает methods=[{devpay + checkout_url}, ...QR если у owner'а
         есть qr_image_url].

    handle_webhook (см. api/payments.py):
      Ищет Payment по provider_ref = intent_id, переводит в paid, публикует
      SSE. HMAC-подпись — Slice 4.
    """

    key = "devpay"

    async def init(self, db: AsyncSession, booking: Booking) -> PayInitResult:
        webhook_url = (
            settings.public_base_app.rstrip("/") + "/api/v1/c/payments/webhook/devpay"
        )
        return_url = settings.public_base_app.rstrip("/") + f"/#/client/pay/{booking.code}"
        order_meta = {
            "merchant_name": "Booking",
            "order_ref": booking.code,
            "booking_id": booking.id,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.devpay_internal_url}/api/v1/intents",
                headers={"Authorization": f"Bearer {settings.devpay_merchant_secret}"},
                json={
                    "amount_kgs": booking.total_kgs,
                    "currency": "KGS",
                    "return_url": return_url,
                    "webhook_url": webhook_url,
                    "order_meta": order_meta,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        payment = Payment(
            booking_id=booking.id,
            provider=PaymentProvider.devpay,
            provider_ref=data["intent_id"],
            amount_kgs=booking.total_kgs,
            status=PaymentStatus.pending,
        )
        db.add(payment)
        await db.flush()

        methods: list[dict[str, Any]] = [
            {
                "key": "devpay",
                "label_key": "pay.method.devpay",
                "checkout_url": data["checkout_url"],
            }
        ]

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


# Singleton. Switch to ELQR (Slice 5 или отдельная story) — заменой этой строки.
provider = DevPayProvider()
