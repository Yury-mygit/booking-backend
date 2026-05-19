"""split confirmed/paid: add bookings.confirmed

Revision ID: add_booking_confirmed
Revises: add_booking_postpay
Create Date: 2026-05-18 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_booking_confirmed"
down_revision: Union[str, None] = "add_booking_postpay"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Backfill: anything already paid (status=paid) is implicitly confirmed.
    op.execute("UPDATE bookings SET confirmed = true WHERE status = 'paid'")


def downgrade() -> None:
    op.drop_column("bookings", "confirmed")
