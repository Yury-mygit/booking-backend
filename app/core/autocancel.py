"""Auto-cancel pending bookings older than N hours (unless postpay=true).

Background task launched via FastAPI lifespan. Runs the sweep every
`INTERVAL_SEC` seconds. Each sweep:
  - SELECT pending+!postpay bookings older than 24h;
  - for each: free availability rows + status=cancelled + SSE refresh.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import AsyncSessionLocal
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Room,
)

log = logging.getLogger("autocancel")

INTERVAL_SEC = 900  # 15 min
EXPIRE_AFTER = timedelta(hours=24)


async def _cancel_one(db: AsyncSession, booking: Booking) -> int:
    """Free availability and mark booking cancelled. Returns hotel_id for SSE."""
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
    hotel_id = (
        await db.execute(select(Room.hotel_id).where(Room.id == booking.room_id))
    ).scalar_one()
    return hotel_id


async def run_sweep() -> int:
    """One sweep — returns number of bookings cancelled."""
    cutoff = datetime.now(timezone.utc) - EXPIRE_AFTER
    count = 0
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Booking)
                .where(
                    Booking.status == BookingStatus.pending,
                    Booking.postpay.is_(False),
                    Booking.created_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        if not rows:
            return 0
        hotel_ids: set[int] = set()
        for booking in rows:
            hid = await _cancel_one(db, booking)
            hotel_ids.add(hid)
            count += 1
            log.info("autocancel: %s (created %s, hotel %s)",
                     booking.code, booking.created_at.isoformat(), hid)
        await db.commit()
        for hid in hotel_ids:
            await pubsub.publish_refresh(hid)
    return count


async def autocancel_loop() -> None:
    log.info("autocancel loop started (interval=%ss, expire=%s)", INTERVAL_SEC, EXPIRE_AFTER)
    # Avoid racing with a fresh container where the migration hasn't applied yet.
    await asyncio.sleep(30)
    while True:
        try:
            n = await run_sweep()
            if n:
                log.info("autocancel: cancelled %d bookings", n)
        except asyncio.CancelledError:
            log.info("autocancel loop cancelled")
            raise
        except Exception:
            log.exception("autocancel sweep failed; will retry next tick")
        try:
            await asyncio.sleep(INTERVAL_SEC)
        except asyncio.CancelledError:
            log.info("autocancel loop cancelled (sleep)")
            raise
