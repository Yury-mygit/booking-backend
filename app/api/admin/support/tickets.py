"""Admin tickets endpoints: список + детали + сообщения + PATCH +
read + assignee shortcuts + tags + audit feed + SSE.

Префикс `/admin/support` навешивается агрегатором в `admin/support/__init__.py`,
а `/admin` — в `admin/__init__.py`. Здесь — только чистые пути.
"""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.models import User
from app.models.support import (
    Ticket,
    TicketEvent,
    TicketMessage,
    TicketPriority,
    TicketSenderKind,
    TicketTag,
    TicketTagAssoc,
)
from app.schemas.support import (
    MessageCreateAdminIn,
    MessageOutAdmin,
    Page,
    TicketCreateAdminIn,
    TicketOutAdmin,
    TicketPatchIn,
)
from app.services.support import events as svc_events
from app.services.support import messages as svc_messages
from app.services.support import notifications as svc_notify
from app.services.support import realtime
from app.services.support import tickets as svc_tickets
from app.services.support.permissions import require_support_agent

from . import _common as conv

router = APIRouter(tags=["admin-support"])

_HEARTBEAT_SECONDS = 30


# ─── helpers ────────────────────────────────────────────────────────


async def _load_ticket_bundle(
    db: AsyncSession, ticket: Ticket
) -> tuple[User, User | None, "TicketCategorySpec", list[TicketTag]]:  # noqa: F821
    user_map = await svc_tickets.get_users_map(db, ids=[ticket.user_id])
    user = user_map[ticket.user_id]
    assignee = None
    if ticket.assignee_id is not None:
        assignee_map = await svc_tickets.get_users_map(db, ids=[ticket.assignee_id])
        assignee = assignee_map.get(ticket.assignee_id)
    cat_map = await svc_tickets.get_categories_map(db, ids=[ticket.category_id])
    category = cat_map[ticket.category_id]
    tags_map = await conv.load_tags_per_ticket(db, [ticket.id])
    tags = tags_map.get(ticket.id, [])
    return user, assignee, category, tags


# ─── list ───────────────────────────────────────────────────────────


@router.get("/tickets", response_model=Page)
async def list_tickets(
    view: str = "active",
    priority: TicketPriority | None = None,
    category_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> Page:
    if limit < 1 or limit > 200:
        raise APIError(400, "bad_limit", "limit must be 1..200")

    tickets_list, total = await svc_tickets.list_admin_tickets(
        db, me_id=ctx.user.id, view=view,
        priority=priority, category_id=category_id, assignee_id=assignee_id,
        search=q, limit=limit, offset=offset,
    )
    if not tickets_list:
        return Page(items=[], total=total, limit=limit, offset=offset)

    ids = [t.id for t in tickets_list]
    user_ids = list({t.user_id for t in tickets_list})
    assignee_ids = list({t.assignee_id for t in tickets_list if t.assignee_id is not None})
    cat_ids = list({t.category_id for t in tickets_list})

    users_map = await svc_tickets.get_users_map(db, ids=user_ids + assignee_ids)
    cat_map = await svc_tickets.get_categories_map(db, ids=cat_ids)
    last_map = await svc_tickets.get_last_messages_map(
        db, ticket_ids=ids, include_internal=True,
    )
    tags_map = await conv.load_tags_per_ticket(db, ids)

    items = [
        conv.list_item_admin(
            t,
            user=users_map[t.user_id],
            assignee=users_map.get(t.assignee_id) if t.assignee_id else None,
            category=cat_map[t.category_id],
            tags=tags_map.get(t.id, []),
            last=last_map.get(t.id),
        )
        for t in tickets_list
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


# ─── get / messages ─────────────────────────────────────────────────


@router.get("/tickets/{number}", response_model=dict)
async def get_ticket(
    number: str,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Возвращает {ticket: TicketOutAdmin, messages: list[MessageOutAdmin]}
    включая internal-сообщения."""
    ticket, msgs = await svc_tickets.get_ticket_with_messages(
        db, number=number, include_internal=True,
    )
    user, assignee, category, tags = await _load_ticket_bundle(db, ticket)
    sender_ids = list({m.sender_user_id for m in msgs})
    sender_map = await svc_tickets.get_users_map(db, ids=sender_ids)

    return {
        "ticket": conv.ticket_out_admin(
            ticket, user=user, assignee=assignee, category=category, tags=tags,
        ).model_dump(mode="json"),
        "messages": [
            conv.message_out_admin(m, sender_map.get(m.sender_user_id)).model_dump(mode="json")
            for m in msgs
        ],
    }


@router.post("/tickets", response_model=TicketOutAdmin, status_code=201)
async def create_ticket_as_admin(
    body: TicketCreateAdminIn,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> TicketOutAdmin:
    """Admin создаёт тикет от имени юзера (source = admin_internal)."""
    from app.models.support import TicketSource

    target_user = await db.get(User, body.user_id)
    if target_user is None:
        raise APIError(404, "user_not_found", f"User {body.user_id} not found")

    ticket, _first_msg = await svc_tickets.create_ticket(
        db, author=target_user,
        category_slug=body.category_slug,
        body=body.body, title=body.title,
        source=TicketSource.admin_internal,
        priority=body.priority,
    )
    await db.commit()
    await db.refresh(ticket)

    realtime.emit_ticket_created(ticket)

    user, assignee, category, tags = await _load_ticket_bundle(db, ticket)
    return conv.ticket_out_admin(ticket, user=user, assignee=assignee, category=category, tags=tags)


@router.post(
    "/tickets/{number}/messages",
    response_model=MessageOutAdmin, status_code=201,
)
async def post_message(
    number: str,
    body: MessageCreateAdminIn,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> MessageOutAdmin:
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    old_status = ticket.status
    msg = await svc_messages.send(
        db, ticket=ticket, sender=ctx.user,
        sender_kind=TicketSenderKind.agent, body=body.body,
        is_internal=body.is_internal,
    )
    await db.commit()
    await db.refresh(msg)
    await db.refresh(ticket)

    realtime.emit_message(ticket, msg)
    if ticket.status != old_status:
        realtime.emit_status_change(ticket, old_status, ticket.status, ctx.user.id)
        asyncio.create_task(svc_notify.notify_user_status_change(ticket.id, ticket.status.value))

    # public message → юзеру в TG; internal — не уведомляем
    if not body.is_internal:
        asyncio.create_task(svc_notify.notify_user_reply(ticket.id, msg.id))

    return conv.message_out_admin(msg, ctx.user)


@router.post("/tickets/{number}/read", status_code=204)
async def mark_read(
    number: str,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    await svc_tickets.mark_read(db, ticket=ticket, side="admin")
    await db.commit()


# ─── PATCH (status / priority / category / assignee / title) ────────


@router.patch("/tickets/{number}", response_model=TicketOutAdmin)
async def patch_ticket(
    number: str,
    body: TicketPatchIn,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> TicketOutAdmin:
    ticket = await svc_tickets.get_ticket_by_number(db, number)

    # sentinel-aware: -1 = «не трогать», None = «снять».
    SENT = -1
    assignee_arg: int | None = (
        None if body.clear_assignee
        else (body.assignee_user_id if body.assignee_user_id is not None else SENT)
    )
    title_arg = body.title if body.title is not None else SENT

    old_status = ticket.status

    ticket = await svc_tickets.patch_ticket(
        db, ticket=ticket, actor=ctx.user,
        status=body.status,
        priority=body.priority,
        category_slug=body.category_slug,
        assignee_user_id=assignee_arg,
        title=title_arg,
    )
    await db.commit()
    await db.refresh(ticket)

    if ticket.status != old_status:
        realtime.emit_status_change(ticket, old_status, ticket.status, ctx.user.id)
        asyncio.create_task(svc_notify.notify_user_status_change(ticket.id, ticket.status.value))
    if body.priority is not None or body.category_slug is not None \
            or assignee_arg != SENT or body.title is not None:
        realtime.emit_admin_meta_change(ticket, "multi", None, None, ctx.user.id)

    user, assignee, category, tags = await _load_ticket_bundle(db, ticket)
    return conv.ticket_out_admin(ticket, user=user, assignee=assignee, category=category, tags=tags)


@router.post("/tickets/{number}/assignee/me", status_code=204)
async def claim_self(
    number: str,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    if ticket.assignee_id == ctx.user.id:
        return
    old = ticket.assignee_id
    await svc_tickets.patch_ticket(
        db, ticket=ticket, actor=ctx.user, assignee_user_id=ctx.user.id,
    )
    await db.commit()
    await db.refresh(ticket)
    realtime.emit_admin_meta_change(ticket, "assignee", old, ctx.user.id, ctx.user.id)


@router.post("/tickets/{number}/assignee/clear", status_code=204)
async def release_assignee(
    number: str,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    if ticket.assignee_id is None:
        return
    old = ticket.assignee_id
    await svc_tickets.patch_ticket(
        db, ticket=ticket, actor=ctx.user, assignee_user_id=None,
    )
    await db.commit()
    await db.refresh(ticket)
    realtime.emit_admin_meta_change(ticket, "assignee", old, None, ctx.user.id)


# ─── tags add/remove ────────────────────────────────────────────────


@router.post("/tickets/{number}/tags", status_code=204)
async def add_tag(
    number: str,
    body: dict,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    tag_id = body.get("tag_id")
    if not isinstance(tag_id, int):
        raise APIError(400, "bad_tag_id", "tag_id (int) required")

    ticket = await svc_tickets.get_ticket_by_number(db, number)
    tag = await db.get(TicketTag, tag_id)
    if tag is None:
        raise APIError(404, "tag_not_found", "Tag not found")

    exists = await db.execute(
        select(TicketTagAssoc).where(
            TicketTagAssoc.ticket_id == ticket.id,
            TicketTagAssoc.tag_id == tag_id,
        )
    )
    if exists.scalar_one_or_none() is not None:
        return  # idempotent

    db.add(TicketTagAssoc(
        ticket_id=ticket.id, tag_id=tag_id, added_by_user_id=ctx.user.id,
    ))
    from app.models.support import TicketEventKind
    await svc_events.log(
        db, ticket_id=ticket.id, actor_user_id=ctx.user.id,
        kind=TicketEventKind.tag_added, payload={"tag_id": tag_id, "tag_name": tag.name},
    )
    ticket.updated_at = datetime.now(timezone.utc)
    await db.commit()

    realtime.emit_admin_meta_change(ticket, "tag", None, tag.name, ctx.user.id)


@router.delete("/tickets/{number}/tags/{tag_id}", status_code=204)
async def remove_tag(
    number: str, tag_id: int,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    tag = await db.get(TicketTag, tag_id)
    if tag is None:
        raise APIError(404, "tag_not_found", "Tag not found")

    row = await db.execute(
        select(TicketTagAssoc).where(
            TicketTagAssoc.ticket_id == ticket.id,
            TicketTagAssoc.tag_id == tag_id,
        )
    )
    assoc = row.scalar_one_or_none()
    if assoc is None:
        return

    await db.delete(assoc)
    from app.models.support import TicketEventKind
    await svc_events.log(
        db, ticket_id=ticket.id, actor_user_id=ctx.user.id,
        kind=TicketEventKind.tag_removed, payload={"tag_id": tag_id, "tag_name": tag.name},
    )
    ticket.updated_at = datetime.now(timezone.utc)
    await db.commit()

    realtime.emit_admin_meta_change(ticket, "tag", tag.name, None, ctx.user.id)


# ─── audit feed ─────────────────────────────────────────────────────


@router.get("/tickets/{number}/events", response_model=list)
async def list_events(
    number: str,
    limit: int = 200,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> list:
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    rows = await db.execute(
        select(TicketEvent).where(TicketEvent.ticket_id == ticket.id)
        .order_by(TicketEvent.created_at.desc()).limit(min(max(limit, 1), 500))
    )
    evts = list(rows.scalars().all())
    actor_ids = list({e.actor_user_id for e in evts if e.actor_user_id is not None})
    actor_map = await svc_tickets.get_users_map(db, ids=actor_ids)
    return [
        conv.event_out(e, actor_map.get(e.actor_user_id)).model_dump(mode="json")
        for e in evts
    ]


# ─── SSE (admin-scoped) ─────────────────────────────────────────────


@router.get("/events/sse")
async def sse_admin(
    request: Request,
    ctx: AuthContext = Depends(require_support_agent),
) -> StreamingResponse:
    async def gen():
        yield "retry: 5000\n\n"
        sub = realtime.subscribe_admin()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(sub.__anext__(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            await sub.aclose()

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
