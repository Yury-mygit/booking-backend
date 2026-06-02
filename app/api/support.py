"""User-side Support API: `/api/v1/support/*`.

Любой залогиненный юзер (client / partner / partner-staff) пишет
в поддержку через эти endpoints. Admin-side endpoints — отдельно
в `api/admin/support/` (Этап 3).

Защита:
- Auth — `Depends(current_user)` (booking session cookie / Bearer).
- Ownership — на каждом запросе проверяется `ticket.user_id == ctx.user.id`.
- is_internal сообщения никогда не уходят клиенту (фильтрация в БД-запросе
  + физическое отсутствие поля в `MessageOutUser`).
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, current_user
from app.core.exceptions import APIError
from app.models.models import Lang, User
from app.models.support import (
    Ticket,
    TicketCategorySpec,
    TicketMessage,
    TicketSenderKind,
    TicketSource,
    TicketStatus,
)
from app.schemas.support import (
    CategoryOut,
    MessageCreateUserIn,
    MessageOutUser,
    Page,
    TicketCreateUserIn,
    TicketListItemUser,
    TicketOutUser,
)
from app.services.support import messages as svc_messages
from app.services.support import realtime
from app.services.support import tickets as svc_tickets

router = APIRouter(prefix="/api/v1/support", tags=["support"])


_HEARTBEAT_SECONDS = 30


# ─── converters ─────────────────────────────────────────────────────


def _category_out(cat: TicketCategorySpec, lang: Lang) -> CategoryOut:
    name = {Lang.ru: cat.name_ru, Lang.en: cat.name_en, Lang.ky: cat.name_ky}[lang]
    return CategoryOut(
        id=cat.id, slug=cat.slug, name=name, icon=cat.icon,
        default_priority=cat.default_priority,
    )


def _msg_out_user(m: TicketMessage) -> MessageOutUser:
    return MessageOutUser(
        id=m.id, ticket_id=m.ticket_id, sender_kind=m.sender_kind,
        body=m.body, created_at=m.created_at, edited_at=m.edited_at,
    )


def _ticket_out_user(t: Ticket, cat: TicketCategorySpec) -> TicketOutUser:
    unread = bool(
        t.last_admin_msg_at
        and (t.user_last_read_at is None or t.user_last_read_at < t.last_admin_msg_at)
    )
    return TicketOutUser(
        number=t.number, title=t.title,
        category=_category_out(cat, t.language),
        status=t.status, language=t.language,
        created_at=t.created_at, updated_at=t.updated_at, closed_at=t.closed_at,
        last_admin_msg_at=t.last_admin_msg_at,
        user_last_read_at=t.user_last_read_at,
        unread_for_user=unread,
    )


def _list_item_user(
    t: Ticket, last: TicketMessage | None, cat: TicketCategorySpec,
) -> TicketListItemUser:
    unread = bool(
        t.last_admin_msg_at
        and (t.user_last_read_at is None or t.user_last_read_at < t.last_admin_msg_at)
    )
    preview = last.body if last else ""
    last_at = last.created_at if last else t.created_at
    return TicketListItemUser(
        number=t.number, title=t.title,
        category=_category_out(cat, t.language),
        status=t.status,
        last_message_preview=preview[:140] + ("…" if len(preview) > 140 else ""),
        last_message_at=last_at,
        unread=unread,
    )


def _ensure_owner(ticket: Ticket, ctx: AuthContext) -> None:
    if ticket.user_id != ctx.user.id:
        # Не раскрываем существование тикета (404, не 403) — чужие тикеты невидимы.
        raise APIError(404, "ticket_not_found", "Ticket not found")


def _detect_source(user: User) -> TicketSource:
    """client/partner_topbar по продуктовой роли. partner-staff считается
    partner_topbar — он работает в partner-блоке SPA."""
    return (
        TicketSource.partner_topbar
        if user.role.value == "partner"
        else TicketSource.client_topbar
    )


# ─── endpoints ──────────────────────────────────────────────────────


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CategoryOut]:
    """Активные категории с именами на языке текущего юзера."""
    cats = await svc_tickets.list_active_categories(db)
    return [_category_out(c, ctx.user.lang) for c in cats]


@router.post("/tickets", response_model=TicketOutUser, status_code=201)
async def create_ticket(
    body: TicketCreateUserIn,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketOutUser:
    """Создание тикета. priority подтянется из category.default_priority.
    Auto-greet system-message добавляется внутри сервиса (если настройка
    включена). После commit'а — broadcast в admin SSE."""
    ticket = await svc_tickets.create_ticket(
        db,
        author=ctx.user,
        category_slug=body.category_slug,
        body=body.body,
        title=body.title,
        source=_detect_source(ctx.user),
    )
    await db.commit()
    await db.refresh(ticket)

    realtime.emit_ticket_created(ticket)

    cat = (await svc_tickets.get_categories_map(db, ids=[ticket.category_id]))[ticket.category_id]
    return _ticket_out_user(ticket, cat)


@router.get("/tickets", response_model=Page)
async def list_my_tickets(
    status: str = "all",  # open | closed | all
    limit: int = 50,
    offset: int = 0,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Page:
    if limit < 1 or limit > 200:
        raise APIError(400, "bad_limit", "limit must be 1..200")
    if status not in {"open", "closed", "all"}:
        raise APIError(400, "bad_status", "status must be open|closed|all")

    tickets_list, total = await svc_tickets.list_user_tickets(
        db, user_id=ctx.user.id, status_filter=status, limit=limit, offset=offset,
    )
    if not tickets_list:
        return Page(items=[], total=total, limit=limit, offset=offset)

    ids = [t.id for t in tickets_list]
    cat_ids = list({t.category_id for t in tickets_list})
    last_map = await svc_tickets.get_last_messages_map(
        db, ticket_ids=ids, include_internal=False,
    )
    cat_map = await svc_tickets.get_categories_map(db, ids=cat_ids)

    items = [_list_item_user(t, last_map.get(t.id), cat_map[t.category_id]) for t in tickets_list]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/tickets/{number}", response_model=dict)
async def get_my_ticket(
    number: str,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Возвращает {ticket: TicketOutUser, messages: list[MessageOutUser]}.
    Internal-сообщения отсутствуют в ответе физически (фильтрация в БД)."""
    ticket, msgs = await svc_tickets.get_ticket_with_messages(
        db, number=number, include_internal=False,
    )
    _ensure_owner(ticket, ctx)
    cat = (await svc_tickets.get_categories_map(db, ids=[ticket.category_id]))[ticket.category_id]
    return {
        "ticket": _ticket_out_user(ticket, cat).model_dump(mode="json"),
        "messages": [_msg_out_user(m).model_dump(mode="json") for m in msgs],
    }


@router.post("/tickets/{number}/messages", response_model=MessageOutUser, status_code=201)
async def post_message(
    number: str,
    body: MessageCreateUserIn,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageOutUser:
    """Юзер пишет в свой тикет. Auto-status transition внутри send()."""
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    _ensure_owner(ticket, ctx)

    old_status = ticket.status
    msg = await svc_messages.send(
        db, ticket=ticket, sender=ctx.user,
        sender_kind=TicketSenderKind.user, body=body.body,
    )
    await db.commit()
    await db.refresh(msg)
    await db.refresh(ticket)

    realtime.emit_message(ticket, msg)
    if ticket.status != old_status:
        realtime.emit_status_change(ticket, old_status, ticket.status, ctx.user.id)

    return _msg_out_user(msg)


@router.post("/tickets/{number}/read", status_code=204)
async def mark_read(
    number: str,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    ticket = await svc_tickets.get_ticket_by_number(db, number)
    _ensure_owner(ticket, ctx)
    await svc_tickets.mark_read(db, ticket=ticket, side="user")
    await db.commit()


# ─── SSE (user-scoped) ──────────────────────────────────────────────


@router.get("/events/sse")
async def sse_user(
    request: Request,
    ctx: AuthContext = Depends(current_user),
) -> StreamingResponse:
    """SSE: новые admin-сообщения (без internal) + статус-изменения
    по моим тикетам. Heartbeat 30с (как в `api/events.py`).
    Caddy для этого пути требует `flush_interval -1`.
    """
    user_id = ctx.user.id

    async def gen():
        yield "retry: 5000\n\n"
        sub = realtime.subscribe_user(user_id)
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
