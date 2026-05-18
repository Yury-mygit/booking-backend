"""add is_superadmin to users

Revision ID: add_user_is_superadmin
Revises: partner_staff_audit
Create Date: 2026-05-18 08:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_user_is_superadmin"
down_revision: Union[str, None] = "partner_staff_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_superadmin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Seed the original superadmin (Yury, id=9). Idempotent: any other user
    # already marked stays marked; nothing else changes.
    op.execute("UPDATE users SET is_superadmin=true WHERE id=9")


def downgrade() -> None:
    op.drop_column("users", "is_superadmin")
