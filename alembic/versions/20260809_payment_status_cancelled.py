"""payment_status — add ENUM value 'cancelled'

DP-4 (Slice 3): user-initiated cancel — новый статус Payment.cancelled
(отличается от declined, который приходит от банка/PSP).

Revision ID: payment_status_cancelled
Revises: payment_provider_devpay
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "payment_status_cancelled"
down_revision: Union[str, None] = "payment_provider_devpay"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # Postgres не поддерживает DROP VALUE из enum. No-op.
    pass
