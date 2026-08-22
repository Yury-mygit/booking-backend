"""TBB-65: SSE-broadcast каталога удобств.

Global (не hotel-scoped): один канал, все partner-подписчики получают
`{"type":"refresh"}` когда админ мутирует каталог. Партнёр перезапрашивает
`/public/amenity-options?section=…` и обновляет форму без reload.

In-memory (single-worker, current dev-compose). Для multi-worker — Redis
или postgres LISTEN/NOTIFY.
"""
import asyncio
from typing import AsyncIterator

_subscribers: set[asyncio.Queue] = set()


async def publish_refresh() -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait({"type": "refresh"})
        except asyncio.QueueFull:
            pass


async def subscribe() -> AsyncIterator[dict]:
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _subscribers.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers.discard(q)
