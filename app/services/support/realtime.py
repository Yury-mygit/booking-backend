"""SSE pubsub для support-чата (карта #92).

In-memory, single-worker — той же концепции что core/pubsub.py.
Два scope'а: user (свой thread по block) и admin (все thread'ы).

`emit_*` зовутся **после успешного commit'а** в API-роуте.
"""

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator


# user → {block: set of queues}; раздельно по block чтобы admin-сообщение
# не пришло user'у в другой block, если он подписан только на один.
_user_subs: dict[tuple[int, str], set[asyncio.Queue]] = defaultdict(set)
_admin_subs: set[asyncio.Queue] = set()


# ─── low-level ────────────────────────────────────────────────────────


def publish_user(user_id: int, block: str, event: dict[str, Any]) -> None:
    for q in list(_user_subs.get((user_id, block), ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow consumer — переподпишется на reconnect


def publish_admin(event: dict[str, Any]) -> None:
    for q in list(_admin_subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def subscribe_user(
    user_id: int, block: str
) -> AsyncIterator[dict[str, Any]]:
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    key = (user_id, block)
    _user_subs[key].add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _user_subs[key].discard(q)
        if not _user_subs[key]:
            _user_subs.pop(key, None)


async def subscribe_admin() -> AsyncIterator[dict[str, Any]]:
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _admin_subs.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _admin_subs.discard(q)


# ─── high-level emit ──────────────────────────────────────────────────


def _preview(body: str, limit: int = 140) -> str:
    body = (body or "").strip()
    return body if len(body) <= limit else body[: limit - 1] + "…"


def emit_message(
    thread_id: int,
    user_id: int,
    block: str,
    message_id: int,
    sender_kind: str,
    body: str,
    created_at_iso: str,
) -> None:
    """Новое сообщение в thread'е. Шлём и владельцу (своему),
    и всем admin'ам."""
    base = {
        "type": "support_message",
        "thread_id": thread_id,
        "user_id": user_id,
        "block": block,
        "message_id": message_id,
        "sender_kind": sender_kind,
        "preview": _preview(body),
        "created_at": created_at_iso,
    }
    publish_user(user_id, block, base)
    publish_admin(base)


def emit_thread_created(
    thread_id: int, user_id: int, block: str
) -> None:
    """Новый thread — admin'ам в список."""
    publish_admin({
        "type": "support_thread_created",
        "thread_id": thread_id,
        "user_id": user_id,
        "block": block,
    })
