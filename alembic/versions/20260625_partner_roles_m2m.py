"""partner roles (M2M) + tri-state staff override + invite simplify

Карта: cards/booking/feature/2026-06-24-staff-roles-and-overrides.md (#135).

Decision phase v2 (2026-06-25):
  - один сотрудник имеет произвольный набор ролей (junction
    partner_staff_role). Effective perm = OR(union ролей) | explicit
    tri-state override на сотруднике.
  - инвайт минимальный: только note + expires (роли/perms не несёт).

Изменения:
  1. CREATE TABLE partner_role.
  2. CREATE TABLE partner_staff_role (PK = staff_id+role_id, CASCADE
     обе FK).
  3. ALTER partner_staff.perm_* (5) → nullable, drop server_default.
     Существующие bool значения сохраняются как explicit override.
  4. DROP partner_staff_invite.perm_* (5). Любые существующие
     prefilled perms на активных инвайтах теряются — инвайт
     приглашает «вступить в команду без прав», админ потом
     раздаёт роли/override.

Revision ID: partner_roles_m2m
Revises: booking_guests_structural
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "partner_roles_m2m"
down_revision: Union[str, None] = "booking_guests_structural"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMS = (
    "perm_manage_hotel",
    "perm_manage_rooms",
    "perm_manage_bookings",
    "perm_manage_staff",
    "perm_chat_with_clients",
)


def upgrade() -> None:
    op.create_table(
        "partner_role",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("perm_manage_hotel", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("perm_manage_rooms", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("perm_manage_bookings", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("perm_manage_staff", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("perm_chat_with_clients", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_user_id", "name",
                            name="uq_partner_role_owner_name"),
    )

    op.create_table(
        "partner_staff_role",
        sa.Column(
            "staff_id", sa.Integer(),
            sa.ForeignKey("partner_staff.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id", sa.Integer(),
            sa.ForeignKey("partner_role.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # partner_staff: perm_* → nullable (tri-state override). Существующие
    # bool значения сохраняются как explicit override.
    for col in _PERMS:
        op.alter_column(
            "partner_staff", col,
            existing_type=sa.Boolean(),
            nullable=True,
            server_default=None,
        )

    # partner_staff_invite: дроп perm_* (инвайт без prefilled prerogatives).
    for col in _PERMS:
        op.drop_column("partner_staff_invite", col)


def downgrade() -> None:
    # Возврат perm_* на invite (NOT NULL DEFAULT false; данные не
    # восстановятся, только структура).
    for col in _PERMS:
        op.add_column(
            "partner_staff_invite",
            sa.Column(col, sa.Boolean(), nullable=False,
                      server_default=sa.text("false")),
        )

    # partner_staff.perm_* → NOT NULL. Backfill NULL → false (на случай
    # если в dev накопились).
    for col in _PERMS:
        op.execute(
            f"UPDATE partner_staff SET {col} = false WHERE {col} IS NULL"
        )
        op.alter_column(
            "partner_staff", col,
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        )

    op.drop_table("partner_staff_role")
    op.drop_table("partner_role")
