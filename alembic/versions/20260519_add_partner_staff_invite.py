"""partner_staff_invite

Revision ID: add_partner_staff_invite
Revises: add_booking_confirmed
Create Date: 2026-05-19 22:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_partner_staff_invite"
down_revision: Union[str, None] = "add_booking_confirmed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "partner_staff_invite",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("perm_manage_hotel", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("perm_manage_rooms", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("perm_manage_bookings", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("perm_manage_staff", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "used_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_partner_staff_invite_owner",
        "partner_staff_invite",
        ["owner_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_partner_staff_invite_owner", table_name="partner_staff_invite")
    op.drop_table("partner_staff_invite")
