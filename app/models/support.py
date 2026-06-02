"""Support Ticketing System — модели.

Карта: open_cards/cards/booking/feature/2026-06-02-support-ticketing-system.md
(Этап 1.1). Все 11 таблиц + 5 enum'ов + sequence закладываются одной
миграцией. UI v1 использует только часть — поля nullable / default'ы
выставлены так, чтобы ничего не пилить через миграции позже
(critical: TicketMessage.is_internal, Ticket.assignee_id,
TicketEvent audit, support_ticket_seq).

Roster агентов вынесен в узкую таблицу SupportAgent по образцу
PartnerStaff — без новых boolean-полей на User.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.models import Base, Lang


# ─── enum'ы ─────────────────────────────────────────────────────────


class TicketStatus(str, enum.Enum):
    open = "open"
    pending_admin = "pending_admin"
    pending_user = "pending_user"
    resolved = "resolved"
    closed = "closed"


class TicketPriority(str, enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class TicketSenderKind(str, enum.Enum):
    user = "user"
    agent = "agent"
    system = "system"


class TicketSource(str, enum.Enum):
    client_topbar = "client_topbar"
    partner_topbar = "partner_topbar"
    admin_internal = "admin_internal"
    api = "api"


class TicketEventKind(str, enum.Enum):
    created = "created"
    status_changed = "status_changed"
    assignee_changed = "assignee_changed"
    priority_changed = "priority_changed"
    category_changed = "category_changed"
    tag_added = "tag_added"
    tag_removed = "tag_removed"
    reopened = "reopened"
    auto_closed = "auto_closed"
    merged = "merged"
    escalated = "escalated"


# ─── roster / settings / справочники ────────────────────────────────


class SupportAgent(Base):
    """Кто из юзеров работает с тикетами поддержки.

    Узкая таблица по образцу PartnerStaff. Soft-delete через
    removed_at — чтобы исторические TicketEvent.actor_user_id и
    Ticket.assignee_id оставались валидны (FK SET NULL/RESTRICT).
    Возврат прежнего агента = создание новой строки.
    """

    __tablename__ = "support_agent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_lead: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    note: Mapped[str | None] = mapped_column(String(256))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    added_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        # Один активный агент на user. UPDATE removed_at = свободен слот.
        Index(
            "ix_support_agent_user_active_uq",
            "user_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index("ix_support_agent_user", "user_id"),
    )


class SupportSettings(Base):
    """Singleton (id=1) — глобальные настройки support-домена.

    Миграция засеивает дефолтную строку. UI /admin/support/settings
    редактирует поля. SLA-часы — нижняя граница; cron auto_close_resolved
    читает auto_close_days.
    """

    __tablename__ = "support_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auto_close_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("7")
    )
    sla_response_low_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("72")
    )
    sla_response_normal_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("24")
    )
    sla_response_high_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("4")
    )
    sla_response_urgent_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    sla_resolution_low_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("336")
    )
    sla_resolution_normal_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("72")
    )
    sla_resolution_high_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("24")
    )
    sla_resolution_urgent_h: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("4")
    )
    auto_greet_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (CheckConstraint("id = 1", name="support_settings_singleton"),)


class TicketCategorySpec(Base):
    """Справочник категорий — динамический, не ENUM.

    Юзер выбирает категорию при создании; админ может править/добавлять.
    Локализация ru/ky/en хранится в строке (мало значений). default_priority
    подставляется в Ticket.priority при создании, если юзер не задал.
    """

    __tablename__ = "ticket_category_spec"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name_ru: Mapped[str] = mapped_column(String(80), nullable=False)
    name_en: Mapped[str] = mapped_column(String(80), nullable=False)
    name_ky: Mapped[str] = mapped_column(String(80), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(32))
    default_priority: Mapped[TicketPriority] = mapped_column(
        ENUM(TicketPriority, name="ticket_priority", create_type=False),
        nullable=False,
        server_default=TicketPriority.normal.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ─── тикет и его содержимое ──────────────────────────────────────────


class Ticket(Base):
    """Один тикет = одно обращение.

    number генерируется в БД при INSERT через
    server_default `'T-' || YYYY || '-' || lpad(nextval(...), 4, '0')`.
    sequence создаётся отдельной командой в миграции.

    user_id (автор) — RESTRICT: исторические тикеты переживают любую
    попытку delete юзера. assignee_id — SET NULL: уход агента не теряет
    тикет.

    Хранятся first_response_due_at / resolution_due_at (рассчитанные
    при создании из priority + SupportSettings) — для быстрого фильтра
    «просроченные» индексом.
    """

    __tablename__ = "ticket"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,
        server_default=text(
            "'T-' || extract(year from now())::text || '-' "
            "|| lpad(nextval('ticket_number_seq')::text, 4, '0')"
        ),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(160))
    category_id: Mapped[int] = mapped_column(
        ForeignKey("ticket_category_spec.id", ondelete="RESTRICT"), nullable=False
    )
    priority: Mapped[TicketPriority] = mapped_column(
        ENUM(TicketPriority, name="ticket_priority", create_type=False),
        nullable=False,
        server_default=TicketPriority.normal.value,
    )
    status: Mapped[TicketStatus] = mapped_column(
        ENUM(TicketStatus, name="ticket_status", create_type=False),
        nullable=False,
        server_default=TicketStatus.open.value,
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    source: Mapped[TicketSource] = mapped_column(
        ENUM(TicketSource, name="ticket_source", create_type=False), nullable=False
    )
    language: Mapped[Lang] = mapped_column(
        ENUM(Lang, name="lang", create_type=False), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_user_msg_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_admin_msg_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ticket_status_updated", "status", "updated_at"),
        Index("ix_ticket_assignee_status", "assignee_id", "status"),
        Index("ix_ticket_user_status", "user_id", "status"),
        Index(
            "ix_ticket_first_response_due",
            "first_response_due_at",
            postgresql_where=text(
                "status IN ('open','pending_admin','pending_user')"
            ),
        ),
        Index(
            "ix_ticket_resolution_due",
            "resolution_due_at",
            postgresql_where=text(
                "status IN ('open','pending_admin','pending_user')"
            ),
        ),
    )


class TicketMessage(Base):
    """Сообщение в тикете.

    is_internal критично заложен с первого дня:
    - user-side API всегда фильтрует WHERE is_internal = false;
    - agent-side видит оба, помечает internal'ы визуально.
    Если ввести позже — риск исторических internal-флагов проставленных
    задним числом, и утечка к юзеру.

    sender_kind = system используется для auto-greet, status-change
    нотификаций внутри thread'а (если решим показывать) — sender_user_id
    в этом случае указывает на «бот»-юзера (берём, например, первого
    superadmin'а или специально посеянного user'а system).
    """

    __tablename__ = "ticket_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    sender_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sender_kind: Mapped[TicketSenderKind] = mapped_column(
        ENUM(TicketSenderKind, name="ticket_sender_kind", create_type=False),
        nullable=False,
    )
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_to_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("ticket_message.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_ticket_message_ticket_created", "ticket_id", "created_at"),)


class TicketAttachment(Base):
    """Модель есть с v1, UI добавляется когда media-сервис интегрирован
    (карта #21). storage_url хранит либо media:asset:<UUID>, либо
    локальный путь — формат resolve'ит фронт.
    """

    __tablename__ = "ticket_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("ticket_message.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_url: Mapped[str] = mapped_column(String(512), nullable=False)

    __table_args__ = (Index("ix_ticket_attachment_message", "message_id"),)


class TicketEvent(Base):
    """Audit log.

    Каждое мутирующее действие пишет одну строку. Без этой таблицы
    позже не восстановить кто/когда менял статус, переназначал, добавлял
    теги — закладываем сразу.

    actor_user_id NULL = system action (cron auto_close, etc).
    payload JSONB — {from, to, message_id, tag_id, ...} в зависимости
    от kind.
    """

    __tablename__ = "ticket_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[TicketEventKind] = mapped_column(
        ENUM(TicketEventKind, name="ticket_event_kind", create_type=False),
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ticket_event_ticket_created", "ticket_id", "created_at"),
        Index("ix_ticket_event_actor_created", "actor_user_id", "created_at"),
    )


# ─── теги и макросы ──────────────────────────────────────────────────


class TicketTag(Base):
    """Тег для группировки/поиска тикетов.

    color — фиксированный hex (#RRGGBB), выбирает админ при создании
    через color-picker (HTML <input type=color>). Палитру можно
    добавить как UI-улучшение, но в БД — свободное поле.
    """

    __tablename__ = "ticket_tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(CHAR(7), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TicketTagAssoc(Base):
    """M:N: тикеты ↔ теги."""

    __tablename__ = "ticket_tag_assoc"

    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("ticket.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("ticket_tag.id", ondelete="CASCADE"), primary_key=True
    )
    added_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_ticket_tag_assoc_tag", "tag_id"),)


class CannedResponse(Base):
    """Макрос — шаблон ответа для агента.

    Модель + endpoints в v1, UI ("Шаблоны" в admin) — v1.5 (закроем
    «coming soon»). body может содержать {{user_first_name}},
    {{ticket_number}} — рендер шаблонов на клиенте при выборе.

    is_global=true — видна всем агентам; false — только автору
    (личные макросы под конкретного агента).
    """

    __tablename__ = "canned_response"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[Lang] = mapped_column(
        ENUM(Lang, name="lang", create_type=False), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("ticket_category_spec.id", ondelete="SET NULL")
    )
    is_global: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
