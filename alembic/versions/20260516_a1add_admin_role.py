"""add admin to user_role enum

Revision ID: a1add_admin_role
Revises: f03f31d9cbe8
Create Date: 2026-05-16 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1add_admin_role"
down_revision: Union[str, None] = "f03f31d9cbe8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'admin'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; would require
    # recreating the type. Leaving as no-op.
    pass
