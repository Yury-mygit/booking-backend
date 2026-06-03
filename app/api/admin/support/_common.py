"""Конвертеры ORM → Pydantic для admin support + batch-loader тегов."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Lang, User
from app.models.support import (
    SupportAgent,
    Ticket,
    TicketCategorySpec,
    TicketEvent,
    TicketMessage,
    TicketTag,
    TicketTagAssoc,
)
from app.schemas.support import (
    AgentOut,
    CategoryOut,
    CategoryOutFull,
    MessageOutAdmin,
    TagOut,
    TicketEventOut,
    TicketListItemAdmin,
    TicketOutAdmin,
    UserMini,
)


def user_mini(u: User | None) -> UserMini | None:
    if u is None:
        return None
    return UserMini(
        id=u.id, telegram_id=u.telegram_id, role=u.role,
        first_name=u.first_name, last_name=u.last_name, username=u.username,
    )


def category_out(cat: TicketCategorySpec, lang: Lang) -> CategoryOut:
    name = {Lang.ru: cat.name_ru, Lang.en: cat.name_en, Lang.ky: cat.name_ky}[lang]
    return CategoryOut(
        id=cat.id, slug=cat.slug, name=name, icon=cat.icon,
        default_priority=cat.default_priority,
    )


def category_out_full(cat: TicketCategorySpec) -> CategoryOutFull:
    return CategoryOutFull(
        id=cat.id, slug=cat.slug,
        name_ru=cat.name_ru, name_en=cat.name_en, name_ky=cat.name_ky,
        icon=cat.icon, default_priority=cat.default_priority,
        is_active=cat.is_active, sort_order=cat.sort_order,
        created_at=cat.created_at,
    )


def tag_out(t: TicketTag) -> TagOut:
    return TagOut(id=t.id, name=t.name, color=t.color)


def message_out_admin(m: TicketMessage, sender: User | None) -> MessageOutAdmin:
    return MessageOutAdmin(
        id=m.id, ticket_id=m.ticket_id,
        sender_user_id=m.sender_user_id, sender=user_mini(sender),
        sender_kind=m.sender_kind, is_internal=m.is_internal,
        body=m.body, created_at=m.created_at, edited_at=m.edited_at,
        reply_to_message_id=m.reply_to_message_id,
    )


def _now_aware():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def ticket_out_admin(
    t: Ticket, *,
    user: User, assignee: User | None,
    category: TicketCategorySpec,
    tags: list[TicketTag],
) -> TicketOutAdmin:
    now = _now_aware()
    unread = bool(
        t.last_user_msg_at
        and (t.admin_last_read_at is None or t.admin_last_read_at < t.last_user_msg_at)
    )
    return TicketOutAdmin(
        id=t.id, number=t.number, title=t.title,
        category=category_out_full(category),
        priority=t.priority, status=t.status,
        source=t.source, language=t.language,
        user=user_mini(user), assignee=user_mini(assignee),
        tags=[tag_out(tg) for tg in tags],
        created_at=t.created_at, updated_at=t.updated_at, closed_at=t.closed_at,
        first_response_at=t.first_response_at,
        last_user_msg_at=t.last_user_msg_at,
        last_admin_msg_at=t.last_admin_msg_at,
        admin_last_read_at=t.admin_last_read_at,
        first_response_due_at=t.first_response_due_at,
        resolution_due_at=t.resolution_due_at,
        unread_for_admin=unread,
        is_response_overdue=(
            t.first_response_due_at is not None
            and t.first_response_at is None
            and t.first_response_due_at < now
        ),
        is_resolution_overdue=(
            t.resolution_due_at is not None
            and t.closed_at is None
            and t.resolution_due_at < now
        ),
    )


def list_item_admin(
    t: Ticket, *,
    user: User, assignee: User | None,
    category: TicketCategorySpec,
    tags: list[TicketTag],
    last: TicketMessage | None,
) -> TicketListItemAdmin:
    now = _now_aware()
    unread = bool(
        t.last_user_msg_at
        and (t.admin_last_read_at is None or t.admin_last_read_at < t.last_user_msg_at)
    )
    preview = last.body if last else ""
    last_at = last.created_at if last else t.created_at
    last_sender = last.sender_kind if last else None
    is_overdue = (
        (t.first_response_due_at is not None
         and t.first_response_at is None
         and t.first_response_due_at < now)
        or (t.resolution_due_at is not None
            and t.closed_at is None
            and t.resolution_due_at < now)
    )
    return TicketListItemAdmin(
        number=t.number, title=t.title,
        category=category_out(category, t.language),
        priority=t.priority, status=t.status,
        user=user_mini(user), assignee=user_mini(assignee),
        tags=[tag_out(tg) for tg in tags],
        last_message_preview=preview[:140] + ("…" if len(preview) > 140 else ""),
        last_message_at=last_at,
        last_message_sender_kind=last_sender,
        unread=unread,
        is_overdue=is_overdue,
    )


def event_out(evt: TicketEvent, actor: User | None) -> TicketEventOut:
    return TicketEventOut(
        id=evt.id, ticket_id=evt.ticket_id,
        actor=user_mini(actor), kind=evt.kind,
        payload=evt.payload or {}, created_at=evt.created_at,
    )


def agent_out(
    a: SupportAgent, *,
    user: User, added_by: User | None, removed_by: User | None,
) -> AgentOut:
    return AgentOut(
        id=a.id, user=user_mini(user), is_lead=a.is_lead, note=a.note,
        added_at=a.added_at, added_by=user_mini(added_by),
        removed_at=a.removed_at, removed_by=user_mini(removed_by),
    )


# ─── batch-loaders ──────────────────────────────────────────────────


async def load_tags_per_ticket(
    db: AsyncSession, ticket_ids: list[int],
) -> dict[int, list[TicketTag]]:
    """Все теги для списка тикетов одним запросом. Возвращает map id→list."""
    if not ticket_ids:
        return {}
    rows = await db.execute(
        select(TicketTagAssoc.ticket_id, TicketTag)
        .join(TicketTag, TicketTag.id == TicketTagAssoc.tag_id)
        .where(TicketTagAssoc.ticket_id.in_(ticket_ids))
    )
    out: dict[int, list[TicketTag]] = {}
    for ticket_id, tag in rows.all():
        out.setdefault(ticket_id, []).append(tag)
    return out
