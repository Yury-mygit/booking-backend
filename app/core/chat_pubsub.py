"""In-memory topic pub-sub for chat SSE.

Topic = строка: `client:<user_id>` или `owner:<owner_user_id>`.

`append_message` публикует одно событие в оба topic'а (получатель-клиент и
получатель-партнёр). Subscriber может слушать несколько topic'ов одной
очередью (`subscribe_many`) — используется в партнёрском канале, который
слушает все доступные owner'ы.

Single-worker only (current dev-compose). Для multi-worker — redis или
postgres LISTEN/NOTIFY. Стилистически идентично `core/pubsub.py` (booking
realtime), но отдельный модуль по семантике: тут топик — строка, там — int.
"""
import asyncio
from collections import defaultdict
from typing import AsyncIterator

_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)


async def publish(topic: str, event: dict) -> None:
    """Broadcast event to all subscribers of this topic. Drops on slow queues."""
    for q in list(_subs.get(topic, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Slow subscriber — drop the event for this one; client may
            # already be disconnected. Will catch up on reconnect via REST list.
            pass


async def subscribe_many(topics: list[str]) -> AsyncIterator[dict]:
    """One queue subscribed to N topics. Always unsubscribes on exit."""
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    topics = list(dict.fromkeys(topics))  # dedup, preserve order
    for t in topics:
        _subs[t].add(q)
    try:
        while True:
            yield await q.get()
    finally:
        for t in topics:
            _subs[t].discard(q)
            if not _subs[t]:
                _subs.pop(t, None)
