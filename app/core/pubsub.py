"""In-memory per-hotel pub-sub for SSE.

Single-worker only (current dev-compose). For multi-worker we'd need redis
or postgres LISTEN/NOTIFY — see history/2026-05-17-booking-realtime-bookings.md.
"""
import asyncio
from collections import defaultdict
from typing import AsyncIterator

_subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)


async def publish_refresh(hotel_id: int) -> None:
    """Convenience: fan-out a `{"type": "refresh"}` event to a hotel's subscribers."""
    await publish(hotel_id, {"type": "refresh"})


async def publish(hotel_id: int, event: dict) -> None:
    """Broadcast event to all subscribers of this hotel. Drops on slow queues."""
    for q in list(_subscribers.get(hotel_id, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Slow subscriber — drop the event for this one; client may
            # already be disconnected. They'll catch up on reconnect/refresh.
            pass


async def subscribe(hotel_id: int) -> AsyncIterator[dict]:
    """Async iterator of events for hotel_id. Always unsubscribes on exit."""
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _subscribers[hotel_id].add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers[hotel_id].discard(q)
        if not _subscribers[hotel_id]:
            _subscribers.pop(hotel_id, None)
