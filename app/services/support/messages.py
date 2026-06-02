"""Lifecycle сообщений: send_message + автоматические переходы статусов.

Таблица переходов (по карте):
- user reply on open                → pending_admin (no change → stays open)
- user reply on pending_user        → pending_admin
- user reply on pending_admin/open  → pending_admin (no-op)
- user reply on resolved/closed     → pending_admin + reopened event
- agent (public) on open            → pending_user + first_response_at
- agent (public) on pending_admin   → pending_user
- agent (public) on pending_user    → pending_user (no-op)
- agent (public) on resolved/closed → no auto change (manual reopen)
- agent (internal) — статус НЕ меняется, last_admin_msg_at НЕ обновляется
- system — статус НЕ меняется

Manual status change — через `tickets.patch_ticket` (PATCH /tickets).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIError
from app.models.support import (
    Ticket,
    TicketEventKind,
    TicketMessage,
    TicketSenderKind,
    TicketStatus,
)
from app.services.support import events


# ─── status transitions ─────────────────────────────────────────────


_USER_NEXT = {
    TicketStatus.open: TicketStatus.open,  # author writes more — stays open
    TicketStatus.pending_admin: TicketStatus.pending_admin,  # already waiting
    TicketStatus.pending_user: TicketStatus.pending_admin,
    TicketStatus.resolved: TicketStatus.pending_admin,
    TicketStatus.closed: TicketStatus.pending_admin,
}
_AGENT_NEXT = {
    TicketStatus.open: TicketStatus.pending_user,
    TicketStatus.pending_admin: TicketStatus.pending_user,
    TicketStatus.pending_user: TicketStatus.pending_user,  # no-op
    TicketStatus.resolved: TicketStatus.resolved,  # manual reopen via PATCH
    TicketStatus.closed: TicketStatus.closed,
}


def _next_status(current: TicketStatus, kind: TicketSenderKind) -> TicketStatus:
    if kind == TicketSenderKind.user:
        return _USER_NEXT[current]
    if kind == TicketSenderKind.agent:
        return _AGENT_NEXT[current]
    return current  # system — без изменений


# ─── send ───────────────────────────────────────────────────────────


async def send(
    db: AsyncSession,
    *,
    ticket: Ticket,
    sender: "User",  # type: ignore[name-defined]  # forward ref through Mapped type
    sender_kind: TicketSenderKind,
    body: str,
    is_internal: bool = False,
) -> TicketMessage:
    """Создать TicketMessage, обновить ticket timestamps + статус +
    audit log. Без commit'а."""
    if is_internal and sender_kind != TicketSenderKind.agent:
        raise APIError(400, "internal_only_for_agent", "Only agent messages can be internal")
    if not body or not body.strip():
        raise APIError(400, "empty_body", "Message body cannot be empty")

    now = datetime.now(timezone.utc)

    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_user_id=sender.id,
        sender_kind=sender_kind,
        is_internal=is_internal,
        body=body,
    )
    db.add(msg)
    await db.flush()

    # Timestamps.
    ticket.updated_at = now
    if sender_kind == TicketSenderKind.user:
        ticket.last_user_msg_at = now
    elif sender_kind == TicketSenderKind.agent and not is_internal:
        ticket.last_admin_msg_at = now
        if ticket.first_response_at is None:
            ticket.first_response_at = now

    # Status transition (skip internal & system).
    if sender_kind == TicketSenderKind.agent and is_internal:
        return msg
    if sender_kind == TicketSenderKind.system:
        return msg

    old_status = ticket.status
    new_status = _next_status(old_status, sender_kind)

    if new_status != old_status:
        ticket.status = new_status
        await events.log(
            db, ticket_id=ticket.id, actor_user_id=sender.id,
            kind=TicketEventKind.status_changed,
            payload={"from": old_status.value, "to": new_status.value, "via": "message"},
        )
        # Reopened-event если user'ом из resolved/closed.
        if (
            sender_kind == TicketSenderKind.user
            and old_status in (TicketStatus.resolved, TicketStatus.closed)
        ):
            await events.log(
                db, ticket_id=ticket.id, actor_user_id=sender.id,
                kind=TicketEventKind.reopened, payload={"from_status": old_status.value},
            )

    return msg


# ─── list ───────────────────────────────────────────────────────────


async def list_for_ticket(
    db: AsyncSession,
    *,
    ticket_id: int,
    include_internal: bool,
    limit: int = 200,
    offset: int = 0,
) -> list[TicketMessage]:
    q = select(TicketMessage).where(TicketMessage.ticket_id == ticket_id)
    if not include_internal:
        q = q.where(TicketMessage.is_internal.is_(False))
    q = q.order_by(TicketMessage.created_at.asc()).limit(limit).offset(offset)
    rows = await db.execute(q)
    return list(rows.scalars().all())
