"""Бизнес-логика тикетов: create / get / list / update / mark_read.

Из API-слоя зовётся напрямую (никаких ORM-операций в endpoint'ах).
Транзакции (commit/rollback) — на стороне вызывающего FastAPI-роута,
здесь только flush для получения автогенерированных значений.

Lifecycle статусов реализован в `messages.send` — там же пишутся
TicketEvent(status_changed / reopened). Здесь — Ticket-уровневые
изменения (PATCH полей admin'ом).
"""

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIError
from app.models.models import Lang, User
from app.models.support import (
    SupportSettings,
    Ticket,
    TicketCategorySpec,
    TicketEventKind,
    TicketMessage,
    TicketPriority,
    TicketSenderKind,
    TicketSource,
    TicketStatus,
)
from app.services.support import events
from app.services.support.sla import compute_due


# ─── load helpers ───────────────────────────────────────────────────


async def get_settings(db: AsyncSession) -> SupportSettings:
    row = await db.execute(select(SupportSettings).where(SupportSettings.id == 1))
    settings = row.scalar_one_or_none()
    if settings is None:
        raise APIError(500, "support_not_initialized", "SupportSettings row missing")
    return settings


async def get_category_by_slug(db: AsyncSession, slug: str) -> TicketCategorySpec:
    row = await db.execute(
        select(TicketCategorySpec).where(
            TicketCategorySpec.slug == slug,
            TicketCategorySpec.is_active.is_(True),
        )
    )
    cat = row.scalar_one_or_none()
    if cat is None:
        raise APIError(404, "category_not_found", f"Active category '{slug}' not found")
    return cat


async def get_ticket_by_number(db: AsyncSession, number: str) -> Ticket:
    row = await db.execute(select(Ticket).where(Ticket.number == number))
    ticket = row.scalar_one_or_none()
    if ticket is None:
        raise APIError(404, "ticket_not_found", f"Ticket {number} not found")
    return ticket


# ─── create ─────────────────────────────────────────────────────────


# Bot greet текст — пока хардкод по языкам. При желании вынесем в
# CannedResponse-like справочник, но это лишний слой для одного шаблона.
_AUTO_GREET: dict[Lang, str] = {
    Lang.ru: "Здравствуйте! Мы получили ваше обращение и скоро ответим.",
    Lang.en: "Hello! We received your request and will respond shortly.",
    Lang.ky: "Салам! Билдирүүңүз кабыл алынды, жакында жооп беребиз.",
}


async def create_ticket(
    db: AsyncSession,
    *,
    author: User,
    category_slug: str,
    body: str,
    source: TicketSource,
    title: str | None = None,
    priority: TicketPriority | None = None,
) -> Ticket:
    """Создаёт Ticket + first user message + auto-greet (если enabled)
    + TicketEvent(created). Без commit'а."""
    category = await get_category_by_slug(db, category_slug)
    settings = await get_settings(db)

    eff_priority = priority or category.default_priority
    resp_due, res_due = compute_due(eff_priority, settings)

    ticket = Ticket(
        user_id=author.id,
        title=title,
        category_id=category.id,
        priority=eff_priority,
        source=source,
        language=author.lang,
        status=TicketStatus.open,
        first_response_due_at=resp_due,
        resolution_due_at=res_due,
        last_user_msg_at=datetime.now(timezone.utc),
    )
    db.add(ticket)
    await db.flush()  # получаем ticket.id и server_default number

    # first user message
    first_msg = TicketMessage(
        ticket_id=ticket.id,
        sender_user_id=author.id,
        sender_kind=TicketSenderKind.user,
        body=body,
    )
    db.add(first_msg)

    await events.log(
        db, ticket_id=ticket.id, actor_user_id=author.id,
        kind=TicketEventKind.created, payload={"source": source.value},
    )

    # auto-greet (system message от имени автора — sender_user_id NOT NULL).
    # sender_kind=system отличает её визуально на клиенте.
    if settings.auto_greet_enabled:
        greet = _AUTO_GREET.get(ticket.language, _AUTO_GREET[Lang.en])
        db.add(TicketMessage(
            ticket_id=ticket.id,
            sender_user_id=author.id,
            sender_kind=TicketSenderKind.system,
            body=greet,
        ))

    await db.flush()
    return ticket


# ─── list (user-side) ────────────────────────────────────────────────


async def list_user_tickets(
    db: AsyncSession,
    *,
    user_id: int,
    status_filter: str = "all",  # "open" | "closed" | "all"
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Ticket], int]:
    """Свои тикеты юзера. Open = всё кроме resolved+closed."""
    base = select(Ticket).where(Ticket.user_id == user_id)
    if status_filter == "open":
        base = base.where(Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed]))
    elif status_filter == "closed":
        base = base.where(Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]))

    total_row = await db.execute(select(func.count()).select_from(base.subquery()))
    total = int(total_row.scalar() or 0)

    rows = await db.execute(
        base.order_by(Ticket.updated_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars().all()), total


# ─── list (admin-side, с saved-views + фильтрами) ────────────────────


_ADMIN_VIEW_FILTERS = {
    "active": lambda q, me_id: q.where(
        Ticket.status.in_([TicketStatus.open, TicketStatus.pending_admin, TicketStatus.pending_user])
    ),
    "mine": lambda q, me_id: q.where(
        and_(
            Ticket.assignee_id == me_id,
            Ticket.status.in_([TicketStatus.open, TicketStatus.pending_admin, TicketStatus.pending_user]),
        )
    ),
    "unassigned": lambda q, me_id: q.where(
        and_(Ticket.assignee_id.is_(None), Ticket.status != TicketStatus.closed)
    ),
    "overdue": lambda q, me_id: q.where(
        and_(
            Ticket.status.in_([TicketStatus.open, TicketStatus.pending_admin, TicketStatus.pending_user]),
            or_(
                Ticket.first_response_due_at < func.now(),
                Ticket.resolution_due_at < func.now(),
            ),
        )
    ),
    "archive": lambda q, me_id: q.where(
        Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed])
    ),
}


async def list_admin_tickets(
    db: AsyncSession,
    *,
    me_id: int,
    view: str = "active",
    priority: TicketPriority | None = None,
    category_id: int | None = None,
    assignee_id: int | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Ticket], int]:
    """Расширенный список для admin Inbox.

    view ∈ {active, mine, unassigned, overdue, archive}. Фильтры priority /
    category_id / assignee_id / search накладываются поверх. search ищет
    по `number || title || user.first_name || user.last_name ||
    user.username`.
    """
    q = select(Ticket)
    view_fn = _ADMIN_VIEW_FILTERS.get(view)
    if view_fn is None:
        raise APIError(400, "bad_view", f"Unknown view '{view}'")
    q = view_fn(q, me_id)

    if priority is not None:
        q = q.where(Ticket.priority == priority)
    if category_id is not None:
        q = q.where(Ticket.category_id == category_id)
    if assignee_id is not None:
        q = q.where(Ticket.assignee_id == assignee_id)
    if search:
        s = f"%{search}%"
        q = q.join(User, User.id == Ticket.user_id).where(
            or_(
                Ticket.number.ilike(s),
                Ticket.title.ilike(s),
                User.first_name.ilike(s),
                User.last_name.ilike(s),
                User.username.ilike(s),
            )
        )

    total_row = await db.execute(select(func.count()).select_from(q.subquery()))
    total = int(total_row.scalar() or 0)

    rows = await db.execute(
        q.order_by(Ticket.updated_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars().all()), total


# ─── update / mark_read ──────────────────────────────────────────────


async def patch_ticket(
    db: AsyncSession,
    *,
    ticket: Ticket,
    actor: User,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    category_slug: str | None = None,
    assignee_user_id: int | None = -1,  # -1 = «не трогать», None = «снять»
    title: str | None = -1,             # тот же sentinel
) -> Ticket:
    """Batch update — один проход, один TicketEvent на каждое поле.
    sentinel -1 для «не трогать» (отличается от None=«снять»).
    """
    now = datetime.now(timezone.utc)
    changes = []

    if status is not None and status != ticket.status:
        changes.append(("status", ticket.status.value, status.value))
        ticket.status = status
        if status == TicketStatus.resolved:
            ticket.closed_at = None  # resolved ≠ closed; closed выставится по auto-close
        if status == TicketStatus.closed:
            ticket.closed_at = now

    if priority is not None and priority != ticket.priority:
        changes.append(("priority", ticket.priority.value, priority.value))
        ticket.priority = priority
        # пересчёт SLA при смене priority
        settings = await get_settings(db)
        resp_due, res_due = compute_due(priority, settings, ticket.created_at)
        ticket.first_response_due_at = resp_due
        ticket.resolution_due_at = res_due

    if category_slug is not None:
        new_cat = await get_category_by_slug(db, category_slug)
        if new_cat.id != ticket.category_id:
            changes.append(("category", str(ticket.category_id), str(new_cat.id)))
            ticket.category_id = new_cat.id

    if assignee_user_id != -1 and assignee_user_id != ticket.assignee_id:
        changes.append(("assignee", str(ticket.assignee_id), str(assignee_user_id)))
        ticket.assignee_id = assignee_user_id

    if title != -1 and title != ticket.title:
        changes.append(("title", ticket.title or "", title or ""))
        ticket.title = title

    ticket.updated_at = now

    for field, old, new in changes:
        kind_map = {
            "status": TicketEventKind.status_changed,
            "priority": TicketEventKind.priority_changed,
            "category": TicketEventKind.category_changed,
            "assignee": TicketEventKind.assignee_changed,
            "title": TicketEventKind.status_changed,  # title нет своего kind — лог под status_changed с payload
        }
        await events.log(
            db, ticket_id=ticket.id, actor_user_id=actor.id,
            kind=kind_map[field],
            payload={"field": field, "from": old, "to": new},
        )

    return ticket


async def mark_read(
    db: AsyncSession,
    *,
    ticket: Ticket,
    side: str,  # "user" | "admin"
    up_to_at: datetime | None = None,
) -> None:
    """Поставить *_last_read_at в `up_to_at` (или now)."""
    now = up_to_at or datetime.now(timezone.utc)
    if side == "user":
        ticket.user_last_read_at = now
    elif side == "admin":
        ticket.admin_last_read_at = now
    else:
        raise APIError(400, "bad_side", f"side must be 'user' or 'admin'")


# ─── loader для side-panel admin'а ───────────────────────────────────


async def get_ticket_with_messages(
    db: AsyncSession, *, number: str, include_internal: bool,
) -> tuple[Ticket, list[TicketMessage]]:
    ticket = await get_ticket_by_number(db, number)
    q = select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)
    if not include_internal:
        q = q.where(TicketMessage.is_internal.is_(False))
    q = q.order_by(TicketMessage.created_at.asc())
    rows = await db.execute(q)
    return ticket, list(rows.scalars().all())


# ─── batch helpers для list-карточек ───────────────────────────────


async def get_last_messages_map(
    db: AsyncSession,
    *,
    ticket_ids: list[int],
    include_internal: bool,
) -> dict[int, TicketMessage]:
    """Последнее сообщение per ticket. Postgres DISTINCT ON (ticket_id)."""
    if not ticket_ids:
        return {}
    q = select(TicketMessage).where(TicketMessage.ticket_id.in_(ticket_ids))
    if not include_internal:
        q = q.where(TicketMessage.is_internal.is_(False))
    q = q.distinct(TicketMessage.ticket_id).order_by(
        TicketMessage.ticket_id, TicketMessage.created_at.desc()
    )
    rows = await db.execute(q)
    return {m.ticket_id: m for m in rows.scalars().all()}


async def get_categories_map(
    db: AsyncSession, *, ids: list[int],
) -> dict[int, TicketCategorySpec]:
    if not ids:
        return {}
    rows = await db.execute(
        select(TicketCategorySpec).where(TicketCategorySpec.id.in_(ids))
    )
    return {c.id: c for c in rows.scalars().all()}


async def get_users_map(
    db: AsyncSession, *, ids: list[int],
) -> dict[int, User]:
    """Batch-loader для UserMini в карточках."""
    if not ids:
        return {}
    rows = await db.execute(select(User).where(User.id.in_(ids)))
    return {u.id: u for u in rows.scalars().all()}


async def list_active_categories(db: AsyncSession) -> list[TicketCategorySpec]:
    rows = await db.execute(
        select(TicketCategorySpec)
        .where(TicketCategorySpec.is_active.is_(True))
        .order_by(TicketCategorySpec.sort_order, TicketCategorySpec.id)
    )
    return list(rows.scalars().all())
