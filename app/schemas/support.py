"""Pydantic-схемы Support Ticketing.

Карта: open_cards/cards/booking/feature/2026-06-02-support-ticketing-system.md
(Этап 1.3). Разделение user-side vs admin-side **на уровне схемы**:
user никогда не видит `is_internal`/`priority`/`assignee`/`tags`/audit
— соответствующие поля просто отсутствуют в `*Out` для user.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.models import Lang, UserRole
from app.models.support import (
    TicketEventKind,
    TicketPriority,
    TicketSenderKind,
    TicketSource,
    TicketStatus,
)


# ─── helpers ────────────────────────────────────────────────────────


class UserMini(BaseModel):
    """Минимальный профиль для шапки/списка/audit."""

    id: int
    telegram_id: int
    role: UserRole
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


class Page(BaseModel):
    """Универсальная страница (вместо cursor — offset/limit для простоты)."""

    items: list
    total: int
    limit: int
    offset: int


# ─── category ───────────────────────────────────────────────────────


class CategoryOut(BaseModel):
    """Карточка для user-side: имя на нужном языке + иконка."""

    id: int
    slug: str
    name: str
    icon: str | None = None
    default_priority: TicketPriority


class CategoryOutFull(BaseModel):
    """Полная карточка для admin CRUD: все локали + флаги."""

    id: int
    slug: str
    name_ru: str
    name_en: str
    name_ky: str
    icon: str | None = None
    default_priority: TicketPriority
    is_active: bool
    sort_order: int
    created_at: datetime


class CategoryCreateIn(BaseModel):
    slug: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_-]+$")
    name_ru: str = Field(min_length=1, max_length=80)
    name_en: str = Field(min_length=1, max_length=80)
    name_ky: str = Field(min_length=1, max_length=80)
    icon: str | None = Field(default=None, max_length=32)
    default_priority: TicketPriority = TicketPriority.normal
    sort_order: int = 0


class CategoryPatchIn(BaseModel):
    name_ru: str | None = Field(default=None, min_length=1, max_length=80)
    name_en: str | None = Field(default=None, min_length=1, max_length=80)
    name_ky: str | None = Field(default=None, min_length=1, max_length=80)
    icon: str | None = Field(default=None, max_length=32)
    default_priority: TicketPriority | None = None
    is_active: bool | None = None
    sort_order: int | None = None


# ─── tag ────────────────────────────────────────────────────────────


class TagOut(BaseModel):
    id: int
    name: str
    color: str  # #RRGGBB


class TagCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class TagPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


# ─── messages ───────────────────────────────────────────────────────


class MessageOutUser(BaseModel):
    """User видит без is_internal — сервер фильтрует на запросе."""

    id: int
    ticket_id: int
    sender_kind: TicketSenderKind
    body: str
    created_at: datetime
    edited_at: datetime | None = None


class MessageOutAdmin(BaseModel):
    id: int
    ticket_id: int
    sender_user_id: int
    sender: UserMini | None = None
    sender_kind: TicketSenderKind
    is_internal: bool
    body: str
    created_at: datetime
    edited_at: datetime | None = None
    reply_to_message_id: int | None = None


class MessageCreateUserIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class MessageCreateAdminIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    is_internal: bool = False
    reply_to_message_id: int | None = None


# ─── ticket: user-side ──────────────────────────────────────────────


class TicketCreateUserIn(BaseModel):
    """Юзер создаёт тикет. priority определяется категорией; source
    проставляется в API в зависимости от того, какой topbar (client/partner)."""

    category_slug: str = Field(min_length=2, max_length=32)
    title: str | None = Field(default=None, max_length=160)
    body: str = Field(min_length=1, max_length=8000)


class TicketOutUser(BaseModel):
    """Полный тикет глазами юзера. Без priority/assignee/tags/due_at/audit."""

    number: str
    title: str | None = None
    category: CategoryOut
    status: TicketStatus
    language: Lang
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    last_admin_msg_at: datetime | None = None
    user_last_read_at: datetime | None = None
    unread_for_user: bool = False  # вычисляется в API: last_admin_msg_at > user_last_read_at


class TicketListItemUser(BaseModel):
    """Карточка в моём списке тикетов."""

    number: str
    title: str | None = None
    category: CategoryOut
    status: TicketStatus
    last_message_preview: str
    last_message_at: datetime
    unread: bool


# ─── ticket: admin-side ─────────────────────────────────────────────


class TicketOutAdmin(BaseModel):
    """Полный тикет глазами agent'а. Включает priority/assignee/due/tags."""

    id: int
    number: str
    title: str | None = None
    category: CategoryOutFull
    priority: TicketPriority
    status: TicketStatus
    source: TicketSource
    language: Lang
    user: UserMini
    assignee: UserMini | None = None
    tags: list[TagOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    first_response_at: datetime | None = None
    last_user_msg_at: datetime | None = None
    last_admin_msg_at: datetime | None = None
    admin_last_read_at: datetime | None = None
    first_response_due_at: datetime | None = None
    resolution_due_at: datetime | None = None
    unread_for_admin: bool = False  # last_user_msg_at > admin_last_read_at
    is_response_overdue: bool = False
    is_resolution_overdue: bool = False


class TicketListItemAdmin(BaseModel):
    """Расширенная карточка для Inbox."""

    number: str
    title: str | None = None
    category: CategoryOut         # на языке юзера
    priority: TicketPriority
    status: TicketStatus
    user: UserMini
    assignee: UserMini | None = None
    tags: list[TagOut] = Field(default_factory=list)
    last_message_preview: str
    last_message_at: datetime
    last_message_sender_kind: TicketSenderKind
    unread: bool
    is_overdue: bool = False  # любая из due'ek просрочена


class TicketPatchIn(BaseModel):
    """Все поля Optional, None = не трогать. Чтобы «снять assignee» —
    передаём `clear_assignee: true` (не None, иначе двусмысленно)."""

    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    category_slug: str | None = None
    assignee_user_id: int | None = None
    clear_assignee: bool = False
    title: str | None = None


class TicketCreateAdminIn(BaseModel):
    """Admin создаёт тикет от имени юзера (на будущее: internal-задачи,
    или вынос обращения из чата отель↔клиент)."""

    user_id: int
    category_slug: str
    priority: TicketPriority | None = None
    title: str | None = Field(default=None, max_length=160)
    body: str = Field(min_length=1, max_length=8000)


# ─── audit feed ─────────────────────────────────────────────────────


class TicketEventOut(BaseModel):
    id: int
    ticket_id: int
    actor: UserMini | None = None
    kind: TicketEventKind
    payload: dict
    created_at: datetime


# ─── support agent (roster) ─────────────────────────────────────────


class AgentOut(BaseModel):
    id: int
    user: UserMini
    is_lead: bool
    note: str | None = None
    added_at: datetime
    added_by: UserMini | None = None
    removed_at: datetime | None = None
    removed_by: UserMini | None = None


class AgentAddIn(BaseModel):
    user_id: int
    is_lead: bool = False
    note: str | None = Field(default=None, max_length=256)


class AgentPatchIn(BaseModel):
    is_lead: bool | None = None
    note: str | None = Field(default=None, max_length=256)


class UserSearchOut(BaseModel):
    """Результат поиска юзера для формы добавления агента."""

    id: int
    telegram_id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    role: UserRole
    is_superadmin: bool


# ─── support settings ───────────────────────────────────────────────


class SupportSettingsOut(BaseModel):
    auto_close_days: int
    sla_response_low_h: int
    sla_response_normal_h: int
    sla_response_high_h: int
    sla_response_urgent_h: int
    sla_resolution_low_h: int
    sla_resolution_normal_h: int
    sla_resolution_high_h: int
    sla_resolution_urgent_h: int
    auto_greet_enabled: bool
    updated_at: datetime


class SupportSettingsPatchIn(BaseModel):
    auto_close_days: int | None = Field(default=None, ge=1, le=365)
    sla_response_low_h: int | None = Field(default=None, ge=1)
    sla_response_normal_h: int | None = Field(default=None, ge=1)
    sla_response_high_h: int | None = Field(default=None, ge=1)
    sla_response_urgent_h: int | None = Field(default=None, ge=1)
    sla_resolution_low_h: int | None = Field(default=None, ge=1)
    sla_resolution_normal_h: int | None = Field(default=None, ge=1)
    sla_resolution_high_h: int | None = Field(default=None, ge=1)
    sla_resolution_urgent_h: int | None = Field(default=None, ge=1)
    auto_greet_enabled: bool | None = None


# ─── canned response ───────────────────────────────────────────────


class CannedOut(BaseModel):
    id: int
    title: str
    body: str
    language: Lang
    category_id: int | None = None
    is_global: bool
    usage_count: int
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime


class CannedCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=8000)
    language: Lang
    category_id: int | None = None
    is_global: bool = True


class CannedPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    body: str | None = Field(default=None, min_length=1, max_length=8000)
    language: Lang | None = None
    category_id: int | None = None
    is_global: bool | None = None
