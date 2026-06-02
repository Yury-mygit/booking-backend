"""Support Ticketing System — 11 таблиц + 5 enum'ов + sequence + bootstrap.

Карта: open_cards/cards/booking/feature/2026-06-02-support-ticketing-system.md
(Этап 1.2). Полная модель одной миграцией:
- SupportAgent (узкая permission-таблица по образцу PartnerStaff)
- SupportSettings (singleton id=1)
- TicketCategorySpec (справочник, не ENUM)
- Ticket (number из БД через server_default + sequence)
- TicketMessage (с is_internal критично с первого дня)
- TicketAttachment (модель есть, UI позже)
- TicketEvent (audit log)
- TicketTag + TicketTagAssoc (M:N)
- CannedResponse (модель + endpoints v1, UI v1.5)

Bootstrap в той же транзакции: 1 строка settings + 5 категорий с
локализациями ru/en/ky + SupportAgent для всех существующих is_superadmin
юзеров (is_lead=true, added_by=themselves).

Revision ID: support_ticketing
Revises: hotel_meals_kind
Create Date: 2026-06-02 19:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CHAR, ENUM, JSONB


revision: str = "support_ticketing"
down_revision: Union[str, None] = "hotel_meals_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum'ы — объявляем с create_type=False для столбцов; create() явно ниже.
ticket_status = ENUM(
    "open", "pending_admin", "pending_user", "resolved", "closed",
    name="ticket_status", create_type=False,
)
ticket_priority = ENUM(
    "low", "normal", "high", "urgent", name="ticket_priority", create_type=False,
)
ticket_sender_kind = ENUM(
    "user", "agent", "system", name="ticket_sender_kind", create_type=False,
)
ticket_source = ENUM(
    "client_topbar", "partner_topbar", "admin_internal", "api",
    name="ticket_source", create_type=False,
)
ticket_event_kind = ENUM(
    "created", "status_changed", "assignee_changed", "priority_changed",
    "category_changed", "tag_added", "tag_removed", "reopened",
    "auto_closed", "merged", "escalated",
    name="ticket_event_kind", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Enum'ы.
    ticket_status.create(bind, checkfirst=True)
    ticket_priority.create(bind, checkfirst=True)
    ticket_sender_kind.create(bind, checkfirst=True)
    ticket_source.create(bind, checkfirst=True)
    ticket_event_kind.create(bind, checkfirst=True)

    # 2) Sequence для Ticket.number.
    op.execute("CREATE SEQUENCE IF NOT EXISTS ticket_number_seq START 1")

    # 3) support_agent (узкая RBAC-таблица по образцу partner_staff).
    op.create_table(
        "support_agent",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("is_lead", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.String(256)),
        sa.Column(
            "added_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "added_by_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "removed_by_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_support_agent_user_active_uq", "support_agent", ["user_id"],
        unique=True, postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_index("ix_support_agent_user", "support_agent", ["user_id"])

    # 4) support_settings (singleton).
    op.create_table(
        "support_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("auto_close_days", sa.Integer, nullable=False, server_default=sa.text("7")),
        sa.Column("sla_response_low_h", sa.Integer, nullable=False, server_default=sa.text("72")),
        sa.Column("sla_response_normal_h", sa.Integer, nullable=False, server_default=sa.text("24")),
        sa.Column("sla_response_high_h", sa.Integer, nullable=False, server_default=sa.text("4")),
        sa.Column("sla_response_urgent_h", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("sla_resolution_low_h", sa.Integer, nullable=False, server_default=sa.text("336")),
        sa.Column("sla_resolution_normal_h", sa.Integer, nullable=False, server_default=sa.text("72")),
        sa.Column("sla_resolution_high_h", sa.Integer, nullable=False, server_default=sa.text("24")),
        sa.Column("sla_resolution_urgent_h", sa.Integer, nullable=False, server_default=sa.text("4")),
        sa.Column("auto_greet_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_by_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint("id = 1", name="support_settings_singleton"),
    )

    # 5) ticket_category_spec (справочник).
    op.create_table(
        "ticket_category_spec",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False, unique=True),
        sa.Column("name_ru", sa.String(80), nullable=False),
        sa.Column("name_en", sa.String(80), nullable=False),
        sa.Column("name_ky", sa.String(80), nullable=False),
        sa.Column("icon", sa.String(32)),
        sa.Column(
            "default_priority", ticket_priority,
            nullable=False, server_default="normal",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    # 6) ticket.
    op.create_table(
        "ticket",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "number", sa.String(16), nullable=False, unique=True,
            server_default=sa.text(
                "'T-' || extract(year from now())::text || '-' "
                "|| lpad(nextval('ticket_number_seq')::text, 4, '0')"
            ),
        ),
        sa.Column(
            "user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("title", sa.String(160)),
        sa.Column(
            "category_id", sa.Integer,
            sa.ForeignKey("ticket_category_spec.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "priority", ticket_priority,
            nullable=False, server_default="normal",
        ),
        sa.Column(
            "status", ticket_status,
            nullable=False, server_default="open",
        ),
        sa.Column(
            "assignee_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("source", ticket_source, nullable=False),
        sa.Column(
            "language",
            ENUM("ru", "ky", "en", name="lang", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("first_response_at", sa.DateTime(timezone=True)),
        sa.Column("last_user_msg_at", sa.DateTime(timezone=True)),
        sa.Column("last_admin_msg_at", sa.DateTime(timezone=True)),
        sa.Column("user_last_read_at", sa.DateTime(timezone=True)),
        sa.Column("admin_last_read_at", sa.DateTime(timezone=True)),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ticket_status_updated", "ticket", ["status", "updated_at"])
    op.create_index("ix_ticket_assignee_status", "ticket", ["assignee_id", "status"])
    op.create_index("ix_ticket_user_status", "ticket", ["user_id", "status"])
    op.create_index(
        "ix_ticket_first_response_due", "ticket", ["first_response_due_at"],
        postgresql_where=sa.text(
            "status IN ('open','pending_admin','pending_user')"
        ),
    )
    op.create_index(
        "ix_ticket_resolution_due", "ticket", ["resolution_due_at"],
        postgresql_where=sa.text(
            "status IN ('open','pending_admin','pending_user')"
        ),
    )

    # 7) ticket_message.
    op.create_table(
        "ticket_message",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ticket_id", sa.Integer,
            sa.ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "sender_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("sender_kind", ticket_sender_kind, nullable=False),
        sa.Column("is_internal", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column(
            "reply_to_message_id", sa.Integer,
            sa.ForeignKey("ticket_message.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_ticket_message_ticket_created", "ticket_message", ["ticket_id", "created_at"])

    # 8) ticket_attachment.
    op.create_table(
        "ticket_attachment",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "message_id", sa.Integer,
            sa.ForeignKey("ticket_message.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("storage_url", sa.String(512), nullable=False),
    )
    op.create_index("ix_ticket_attachment_message", "ticket_attachment", ["message_id"])

    # 9) ticket_event (audit log).
    op.create_table(
        "ticket_event",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ticket_id", sa.Integer,
            sa.ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "actor_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("kind", ticket_event_kind, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_ticket_event_ticket_created", "ticket_event", ["ticket_id", "created_at"])
    op.create_index("ix_ticket_event_actor_created", "ticket_event", ["actor_user_id", "created_at"])

    # 10) ticket_tag + assoc.
    op.create_table(
        "ticket_tag",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("color", CHAR(7), nullable=False),
        sa.Column(
            "created_by_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_table(
        "ticket_tag_assoc",
        sa.Column(
            "ticket_id", sa.Integer,
            sa.ForeignKey("ticket.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "tag_id", sa.Integer,
            sa.ForeignKey("ticket_tag.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "added_by_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "added_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_ticket_tag_assoc_tag", "ticket_tag_assoc", ["tag_id"])

    # 11) canned_response.
    op.create_table(
        "canned_response",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "language",
            ENUM("ru", "ky", "en", name="lang", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "category_id", sa.Integer,
            sa.ForeignKey("ticket_category_spec.id", ondelete="SET NULL"),
        ),
        sa.Column("is_global", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_by_user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("usage_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    # 12) Bootstrap данных.
    # 12.1) support_settings (id=1) — все дефолты подхватятся server_default.
    op.execute("INSERT INTO support_settings (id) VALUES (1)")

    # 12.2) 5 категорий с локализациями.
    op.execute("""
        INSERT INTO ticket_category_spec
            (slug, name_ru, name_en, name_ky, icon, default_priority, sort_order)
        VALUES
            ('booking',   'Бронирование',  'Booking',   'Брондоо',         'calendar',    'normal', 10),
            ('payment',   'Оплата',        'Payment',   'Төлөм',           'credit-card', 'high',   20),
            ('account',   'Аккаунт',       'Account',   'Каттык эсеп',     'user',        'normal', 30),
            ('technical', 'Техническая',   'Technical', 'Техникалык',      'wrench',      'normal', 40),
            ('other',     'Другое',        'Other',     'Башка',           'help-circle', 'low',    99)
    """)

    # 12.3) SupportAgent для всех существующих superadmin'ов (is_lead=true,
    # added_by=themselves чтобы FK RESTRICT не упёрся в NULL).
    op.execute("""
        INSERT INTO support_agent (user_id, is_lead, added_by_user_id, note)
        SELECT id, true, id, 'Bootstrap: existing superadmin'
        FROM users
        WHERE is_superadmin = true
    """)


def downgrade() -> None:
    # Tables в обратном порядке (от листьев к корням).
    op.drop_table("canned_response")
    op.drop_index("ix_ticket_tag_assoc_tag", table_name="ticket_tag_assoc")
    op.drop_table("ticket_tag_assoc")
    op.drop_table("ticket_tag")
    op.drop_index("ix_ticket_event_actor_created", table_name="ticket_event")
    op.drop_index("ix_ticket_event_ticket_created", table_name="ticket_event")
    op.drop_table("ticket_event")
    op.drop_index("ix_ticket_attachment_message", table_name="ticket_attachment")
    op.drop_table("ticket_attachment")
    op.drop_index("ix_ticket_message_ticket_created", table_name="ticket_message")
    op.drop_table("ticket_message")
    op.drop_index("ix_ticket_resolution_due", table_name="ticket")
    op.drop_index("ix_ticket_first_response_due", table_name="ticket")
    op.drop_index("ix_ticket_user_status", table_name="ticket")
    op.drop_index("ix_ticket_assignee_status", table_name="ticket")
    op.drop_index("ix_ticket_status_updated", table_name="ticket")
    op.drop_table("ticket")
    op.drop_table("ticket_category_spec")
    op.drop_table("support_settings")
    op.drop_index("ix_support_agent_user", table_name="support_agent")
    op.drop_index("ix_support_agent_user_active_uq", table_name="support_agent")
    op.drop_table("support_agent")

    op.execute("DROP SEQUENCE IF EXISTS ticket_number_seq")

    ticket_event_kind.drop(op.get_bind(), checkfirst=True)
    ticket_source.drop(op.get_bind(), checkfirst=True)
    ticket_sender_kind.drop(op.get_bind(), checkfirst=True)
    ticket_priority.drop(op.get_bind(), checkfirst=True)
    ticket_status.drop(op.get_bind(), checkfirst=True)
