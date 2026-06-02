"""SSE pubsub для support — два scope'а: user (свой тред) и admin
(все треды). In-memory, single-worker — той же концепции что
`core/pubsub.py`, но изолированно от per-hotel инфраструктуры.

Высокоуровневые `emit_*` зовутся **после успешного commit'а** в
API-роуте — иначе rollback'нувший event достанется подписчикам.
"""

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator


_user_subs: dict[int, set[asyncio.Queue]] = defaultdict(set)
_admin_subs: set[asyncio.Queue] = set()


# ─── low-level ──────────────────────────────────────────────────────


def publish_user(user_id: int, event: dict[str, Any]) -> None:
    for q in list(_user_subs.get(user_id, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow consumer — let them catch up on reconnect


def publish_admin(event: dict[str, Any]) -> None:
    for q in list(_admin_subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def subscribe_user(user_id: int) -> AsyncIterator[dict[str, Any]]:
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    _user_subs[user_id].add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _user_subs[user_id].discard(q)
        if not _user_subs[user_id]:
            _user_subs.pop(user_id, None)


async def subscribe_admin() -> AsyncIterator[dict[str, Any]]:
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _admin_subs.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _admin_subs.discard(q)


# ─── high-level emit ────────────────────────────────────────────────


def _preview(body: str, limit: int = 140) -> str:
    body = body.strip()
    return body if len(body) <= limit else body[: limit - 1] + "…"


def emit_message(ticket, msg) -> None:
    """Новое сообщение. Internal-msg админу да, юзеру — нет."""
    base_event = {
        "type": "ticket_message",
        "ticket_id": ticket.id,
        "ticket_number": ticket.number,
        "message_id": msg.id,
        "sender_kind": msg.sender_kind.value,
        "preview": _preview(msg.body),
        "created_at": msg.created_at.isoformat(),
    }
    # User видит только не-internal.
    if not msg.is_internal:
        publish_user(ticket.user_id, base_event)
    # Admin видит всё, но с пометкой.
    publish_admin({**base_event, "is_internal": msg.is_internal, "user_id": ticket.user_id})


def emit_status_change(ticket, old_status, new_status, actor_user_id: int | None) -> None:
    base = {
        "type": "ticket_status_changed",
        "ticket_id": ticket.id,
        "ticket_number": ticket.number,
        "from": old_status.value,
        "to": new_status.value,
        "actor_user_id": actor_user_id,
    }
    publish_user(ticket.user_id, base)
    publish_admin({**base, "user_id": ticket.user_id})


def emit_ticket_created(ticket) -> None:
    """Новый тикет — только админам (юзер сам знает что создал)."""
    publish_admin({
        "type": "ticket_created",
        "ticket_id": ticket.id,
        "ticket_number": ticket.number,
        "user_id": ticket.user_id,
        "priority": ticket.priority.value,
        "category_id": ticket.category_id,
    })


def emit_admin_meta_change(ticket, field: str, old, new, actor_user_id: int) -> None:
    """Изменения priority/assignee/category/tags — только админам."""
    publish_admin({
        "type": "ticket_meta_changed",
        "ticket_id": ticket.id,
        "ticket_number": ticket.number,
        "field": field,
        "from": str(old) if old is not None else None,
        "to": str(new) if new is not None else None,
        "actor_user_id": actor_user_id,
    })
