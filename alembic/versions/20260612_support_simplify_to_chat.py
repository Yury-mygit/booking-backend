"""Support simplify to chat (карта #92).

Полный refactor support-домена: drop тикетинговой инфраструктуры
(10 таблиц + sequence + 5 enum'ов), create простой chat (2 таблицы +
2 enum'а). Thread keyed по (user_id, block∈{client,partner}).

Реальных тикетов в prod нет (подтверждено Yury 2026-06-12) —
data migration не делаем. Downgrade не поддержан one-way.

Revision ID: support_simplify_to_chat
Revises: support_ticketing
Create Date: 2026-06-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "support_simplify_to_chat"
down_revision: Union[str, None] = "support_ticketing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── drop тикетинга ─────────────────────────────────────────────
    # FK-зависимости разрешаем в обратном порядке:
    #   ticket_event/ticket_message/ticket_attachment/ticket_tag_assoc
    #     → ticket; canned_response/ticket → ticket_category_spec.
    op.drop_table("ticket_event")
    op.drop_table("ticket_attachment")
    op.drop_table("ticket_message")
    op.drop_table("ticket_tag_assoc")
    op.drop_table("ticket_tag")
    op.drop_table("canned_response")
    op.drop_table("ticket")
    op.drop_table("ticket_category_spec")
    op.drop_table("support_settings")
    op.drop_table("support_agent")

    op.execute("DROP SEQUENCE IF EXISTS ticket_number_seq")

    for enum_name in (
        "ticket_event_kind",
        "ticket_source",
        "ticket_sender_kind",
        "ticket_priority",
        "ticket_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

    # ─── новые enum'ы ───────────────────────────────────────────────
    support_block = ENUM(
        "client", "partner", name="support_block", create_type=False
    )
    support_sender_kind = ENUM(
        "user", "admin", name="support_sender_kind", create_type=False
    )
    support_block.create(op.get_bind(), checkfirst=False)
    support_sender_kind.create(op.get_bind(), checkfirst=False)

    # ─── новые таблицы ──────────────────────────────────────────────
    op.create_table(
        "support_thread",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("block", support_block, nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("user_last_read_at", sa.DateTime(timezone=True)),
        sa.Column("admin_last_read_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "block", name="uq_support_thread_user_block"
        ),
    )
    op.create_index(
        "ix_support_thread_last_msg",
        "support_thread",
        ["last_message_at"],
    )
    op.create_index(
        "ix_support_thread_user",
        "support_thread",
        ["user_id"],
    )

    op.create_table(
        "support_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "thread_id",
            sa.Integer(),
            sa.ForeignKey("support_thread.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sender_kind", support_sender_kind, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_support_message_thread_created",
        "support_message",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade не поддержан: тикетинговая инфраструктура снесена "
        "one-way через карту #92. Чтобы вернуться к support_ticketing, "
        "удалить новые таблицы вручную и применить миграцию заново."
    )
