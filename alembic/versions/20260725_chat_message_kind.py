"""chat_messages.kind — тип сообщения (user | system_*)

TBB-31 backend: dedup «есть заявка на отмену» + различение системных
сообщений от пользовательских в чате client↔hotel.

Enum значения:
  - user (default для всех существующих)
  - cancellation_request

Backfill: server_default='user' → все существующие строки станут 'user'
без явного UPDATE.

Revision ID: chat_message_kind
Revises: staff_role_per_hotel_scope
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "chat_message_kind"
down_revision: Union[str, None] = "staff_role_per_hotel_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE chat_message_kind AS ENUM ('user', 'cancellation_request')"
    )
    op.add_column(
        "chat_messages",
        sa.Column(
            "kind",
            sa.Enum("user", "cancellation_request", name="chat_message_kind", create_type=False),
            nullable=False,
            server_default="user",
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "kind")
    op.execute("DROP TYPE chat_message_kind")
