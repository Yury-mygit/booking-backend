"""drop sessions.role column

Variant B single-token model: session не носит роли — права считаются
per-endpoint по user.role + accessible_owners. Поле sessions.role
осталось от ранней схемы (role-by-entry-point), мёртвый груз.

Revision ID: drop_session_role
Revises: add_user_qr_image_url
Create Date: 2026-05-26 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "drop_session_role"
down_revision: Union[str, None] = "add_user_qr_image_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("sessions", "role")


def downgrade() -> None:
    user_role = ENUM(
        "client", "partner", "admin",
        name="user_role",
        create_type=False,
    )
    op.add_column(
        "sessions",
        sa.Column(
            "role",
            user_role,
            nullable=False,
            server_default="client",
        ),
    )
    op.alter_column("sessions", "role", server_default=None)
