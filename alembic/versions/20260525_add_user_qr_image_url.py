"""user_qr_image_url

Revision ID: add_user_qr_image_url
Revises: add_partner_staff_invite
Create Date: 2026-05-25 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "add_user_qr_image_url"
down_revision: Union[str, None] = "add_partner_staff_invite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("qr_image_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "qr_image_url")
