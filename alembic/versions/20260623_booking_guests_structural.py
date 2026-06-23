"""bookings.guests → structural (adults/children/infants/child_ages)

Карта: cards/booking/feature/2026-06-22-guests-filter-picker.md (#125).

Batched approach (single-statement add NOT NULL не подойдёт — нужна
backfill из старого guests):
  1. add adults NULL, children/infants NOT NULL DEFAULT 0,
     child_ages JSONB NULL.
  2. UPDATE bookings SET adults = guests — backfill.
  3. ALTER adults SET NOT NULL + server_default '1' (для будущих
     INSERT без значения, API всё равно явно передаёт).
  4. DROP guests.

Subdecisions (см. карту):
  - default existing rows: adults=guests, children=0, infants=0,
    child_ages=null.
  - child_ages: nullable JSONB list[int]; собирается когда children>0.

Revision ID: booking_guests_structural
Revises: room_status
Create Date: 2026-06-23 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "booking_guests_structural"
down_revision: Union[str, None] = "room_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bookings", sa.Column("adults", sa.Integer(), nullable=True)
    )
    op.add_column(
        "bookings",
        sa.Column(
            "children", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "infants", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "bookings",
        sa.Column("child_ages", postgresql.JSONB(), nullable=True),
    )

    op.execute("UPDATE bookings SET adults = guests")

    op.alter_column(
        "bookings", "adults", nullable=False, server_default="1"
    )

    op.drop_column("bookings", "guests")


def downgrade() -> None:
    op.add_column(
        "bookings", sa.Column("guests", sa.Integer(), nullable=True)
    )
    op.execute("UPDATE bookings SET guests = adults")
    op.alter_column(
        "bookings", "guests", nullable=False, server_default="1"
    )
    op.drop_column("bookings", "child_ages")
    op.drop_column("bookings", "infants")
    op.drop_column("bookings", "children")
    op.drop_column("bookings", "adults")
