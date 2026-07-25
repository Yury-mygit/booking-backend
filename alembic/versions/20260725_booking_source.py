"""bookings.source — enum(online | walkin), backfill postpay=true → walkin, DROP postpay

TBB-24: postpay-концепт удаляется из модели/API/UI. Historical метка walkin
сохраняется в новом поле `Booking.source` для будущего audit (см. decision_md).

Порядок в одной ревизии:
  1. CREATE TYPE booking_source AS ENUM ('online', 'walkin').
  2. ADD COLUMN bookings.source NOT NULL DEFAULT 'online'.
  3. UPDATE bookings SET source='walkin' WHERE postpay=true.
  4. DROP COLUMN bookings.postpay.

Revision ID: booking_source
Revises: chat_message_kind
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "booking_source"
down_revision: Union[str, None] = "chat_message_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE booking_source AS ENUM ('online', 'walkin')")
    op.add_column(
        "bookings",
        sa.Column(
            "source",
            sa.Enum("online", "walkin", name="booking_source", create_type=False),
            nullable=False,
            server_default="online",
        ),
    )
    op.execute("UPDATE bookings SET source='walkin' WHERE postpay=true")
    op.drop_column("bookings", "postpay")


def downgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "postpay",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute("UPDATE bookings SET postpay=true WHERE source='walkin'")
    op.drop_column("bookings", "source")
    op.execute("DROP TYPE booking_source")
