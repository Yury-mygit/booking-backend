"""updated_at column for core mutable tables — TBB-33

Scope B: Booking, Room, Payment, PartnerStaff, User.
Second-tier mutable models (HotelService, Availability, PartnerStaffInvite,
ChatThread, PartnerProfile) — отдельная follow-up story.

Backfill: `updated_at = created_at` (playbook §2.1 default).

Revision ID: updated_at_core_mutable
Revises: webhook_idempotency
Create Date: 2026-08-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "updated_at_core_mutable"
down_revision: Union[str, None] = "webhook_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = ("users", "rooms", "bookings", "payments", "partner_staff")


def upgrade() -> None:
    for tbl in TABLES:
        op.add_column(
            tbl,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        op.execute(f"UPDATE {tbl} SET updated_at = created_at")


def downgrade() -> None:
    for tbl in reversed(TABLES):
        op.drop_column(tbl, "updated_at")
