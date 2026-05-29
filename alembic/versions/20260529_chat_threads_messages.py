"""chat_threads + chat_messages + partner_staff.perm_chat_with_clients

Карта: open_cards/cards/booking/feature/2026-05-28-client-hotel-chat.md
(этап 2.1-2.2). См. R10 за полной схемой.

- Тред = (hotel_id, client_user_id), уникальный.
- Сообщение опционально несёт subject (hotel/booking/room) — тег "о чём".
- Read-receipts: *_last_read_at на треде, не per-message.
- Новый perm-флаг для staff: chat_with_clients (default false).

Revision ID: chat_threads_messages
Revises: drop_session_role
Create Date: 2026-05-29 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "chat_threads_messages"
down_revision: Union[str, None] = "drop_session_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sender_kind = ENUM("client", "hotel", name="chat_sender_kind", create_type=False)
    subject_type = ENUM(
        "hotel", "booking", "room", name="chat_subject_type", create_type=False
    )
    sender_kind.create(op.get_bind(), checkfirst=True)
    subject_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "chat_threads",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "hotel_id",
            sa.Integer,
            sa.ForeignKey("hotels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("client_last_read_at", sa.DateTime(timezone=True)),
        sa.Column("hotel_last_read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "hotel_id", "client_user_id", name="uq_chat_threads_hotel_client"
        ),
    )
    op.create_index(
        "ix_chat_threads_hotel_last_msg",
        "chat_threads",
        ["hotel_id", sa.text("last_message_at DESC")],
    )
    op.create_index(
        "ix_chat_threads_client_last_msg",
        "chat_threads",
        ["client_user_id", sa.text("last_message_at DESC")],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "thread_id",
            sa.BigInteger,
            sa.ForeignKey("chat_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_kind", sender_kind, nullable=False),
        sa.Column(
            "sender_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("subject_type", subject_type),
        sa.Column("subject_id", sa.Integer),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chat_messages_thread_created",
        "chat_messages",
        ["thread_id", "created_at"],
    )

    op.add_column(
        "partner_staff",
        sa.Column(
            "perm_chat_with_clients",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "partner_staff_invite",
        sa.Column(
            "perm_chat_with_clients",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("partner_staff_invite", "perm_chat_with_clients")
    op.drop_column("partner_staff", "perm_chat_with_clients")
    op.drop_index("ix_chat_messages_thread_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_threads_client_last_msg", table_name="chat_threads")
    op.drop_index("ix_chat_threads_hotel_last_msg", table_name="chat_threads")
    op.drop_table("chat_threads")
    op.execute("DROP TYPE IF EXISTS chat_subject_type")
    op.execute("DROP TYPE IF EXISTS chat_sender_kind")
