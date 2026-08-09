"""payment_provider — rename ENUM value 'mock' → 'devpay'

DP-3 (Slice 2): mock instant-provider удаляется, единый provider — devpay
(sandbox PSP). Alembic ALTER TYPE переписывает enum in-place, existing rows
получают новое значение автоматически.

Revision ID: payment_provider_devpay
Revises: hotels_photos_normalize
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "payment_provider_devpay"
down_revision: Union[str, None] = "hotels_photos_normalize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE payment_provider RENAME VALUE 'mock' TO 'devpay'")


def downgrade() -> None:
    op.execute("ALTER TYPE payment_provider RENAME VALUE 'devpay' TO 'mock'")
