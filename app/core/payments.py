"""Payment providers.

Booking → PSP через тонкий интерфейс: `init` выдаёт страницу оплаты для
брони, `handle_webhook` вызывается когда PSP присылает webhook о финальном
статусе.

`init` идемпотентен: у брони не больше одного живого intent'а. Если есть
pending-платёж, его статус сверяется с PSP — открытый intent отдаётся
повторно, оплаченный (webhook ещё в пути) → 409 `payment_processing`,
закрытый отказом/отменой помечается локально и выписывается новый. Иначе
две открытые страницы оплаты = риск двойного списания.

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
from app.core.exceptions import APIError
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


# Статус intent'а DevPay → локальный статус платежа для закрытых отказом.
_CLOSED_INTENT_STATUS = {
    "declined": PaymentStatus.failed,
    "expired": PaymentStatus.failed,
    "cancelled": PaymentStatus.cancelled,
}


class DevPayProvider:
    """Sandbox PSP через HTTP intents API.

    init:
      1. Есть pending-платёж с checkout_url → GET intent у DevPay:
         created → вернуть тот же checkout; paid → 409 payment_processing;
         declined/expired/cancelled → пометить платёж и идти в п.2.
      2. POST DEVPAY_INTERNAL_URL/api/v1/intents с Bearer, получает
         {intent_id, checkout_url}; сохраняет Payment(pending,
         provider=devpay, provider_ref=intent_id, checkout_url).
      3. Возвращает methods=[{devpay + checkout_url}, ...QR если у owner'а
         есть qr_image_url].

    Вызывающий держит блокировку строки брони — параллельные init'ы
    одной брони сериализуются.

    handle_webhook (см. api/payments.py):
      Ищет Payment по provider_ref = intent_id, переводит в paid, публикует
      SSE. HMAC-подпись — Slice 4.
    """

    key = "devpay"

    async def init(self, db: AsyncSession, booking: Booking) -> PayInitResult:
        pending = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.booking_id == booking.id,
                    Payment.provider == PaymentProvider.devpay,
                    Payment.status == PaymentStatus.pending,
                )
                .order_by(Payment.created_at.desc())
            )
        ).scalars().all()

        reusable: Payment | None = None
        for payment in pending:
            if payment.checkout_url is None:
                # Платёж до payments_checkout_url: страницу не вернуть.
                continue
            intent_status = await self._intent_status(payment.provider_ref)
            if intent_status == "created" and reusable is None:
                reusable = payment
            elif intent_status == "paid":
                raise APIError(
                    409, "payment_processing",
                    "Payment accepted by PSP, confirmation pending",
                )
            elif intent_status in _CLOSED_INTENT_STATUS:
                payment.status = _CLOSED_INTENT_STATUS[intent_status]

        if reusable is None:
            reusable = await self._create_intent(db, booking)

        return PayInitResult(
            payment_id=reusable.id,
            amount_kgs=reusable.amount_kgs,
            provider=self.key,
            methods=await self._methods(db, booking, reusable.checkout_url),
        )

    async def _create_intent(self, db: AsyncSession, booking: Booking) -> Payment:
        webhook_url = (
            settings.public_base_app.rstrip("/") + "/api/v1/c/payments/webhook/devpay"
        )
        return_url = settings.public_base_app.rstrip("/") + f"/#/client/pay/{booking.code}"
        order_meta = {
            "merchant_name": "Booking",
            "order_ref": booking.code,
            "booking_id": booking.id,
        }
        data = await self._request(
            "POST",
            "/api/v1/intents",
            json={
                "amount_kgs": booking.total_kgs,
                "currency": "KGS",
                "return_url": return_url,
                "webhook_url": webhook_url,
                "order_meta": order_meta,
            },
        )
        payment = Payment(
            booking_id=booking.id,
            provider=PaymentProvider.devpay,
            provider_ref=data["intent_id"],
            checkout_url=data["checkout_url"],
            amount_kgs=booking.total_kgs,
            status=PaymentStatus.pending,
        )
        db.add(payment)
        await db.flush()
        return payment

    async def _intent_status(self, intent_id: str) -> str:
        data = await self._request("GET", f"/api/v1/intents/{intent_id}")
        return data["status"]

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """HTTP к DevPay; сетевой сбой или не-2xx → 502 psp_unavailable."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.request(
                    method,
                    f"{settings.devpay_internal_url}{path}",
                    headers={"Authorization": f"Bearer {settings.devpay_merchant_secret}"},
                    **kwargs,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise APIError(
                502, "psp_unavailable", "Payment provider is unavailable"
            ) from exc

    async def _methods(
        self, db: AsyncSession, booking: Booking, checkout_url: str
    ) -> list[dict[str, Any]]:
        methods: list[dict[str, Any]] = [
            {
                "key": "devpay",
                "label_key": "pay.method.devpay",
                "checkout_url": checkout_url,
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
        return methods


# Singleton. Switch to ELQR (Slice 5 или отдельная story) — заменой этой строки.
provider = DevPayProvider()
