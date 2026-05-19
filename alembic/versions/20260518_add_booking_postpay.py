"""add postpay flag to bookings

Revision ID: add_booking_postpay
Revises: add_user_is_superadmin
Create Date: 2026-05-18 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_booking_postpay"
down_revision: Union[str, None] = "add_user_is_superadmin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "postpay",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("bookings", "postpay")
