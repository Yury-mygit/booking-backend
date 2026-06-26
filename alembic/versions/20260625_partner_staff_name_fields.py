"""partner_staff — 3 nullable name fields (first/last/middle)

Карта: cards/booking/feature/2026-06-25-staff-add-name-fields.md (#136).

Партнёр в quick-add вписывает ФИО сотрудника напрямую (D1). Все три
поля nullable: пустой quick-add (только telegram_id) валиден — тогда
display fallback на `users.first_name` (TG-имя). Backfill NULL —
существующие записи не пытаемся угадать из TG (full model, не
data-massage).

Revision ID: partner_staff_name_fields
Revises: partner_roles_m2m
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "partner_staff_name_fields"
down_revision: Union[str, None] = "partner_roles_m2m"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "partner_staff",
        sa.Column("first_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "partner_staff",
        sa.Column("last_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "partner_staff",
        sa.Column("middle_name", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("partner_staff", "middle_name")
    op.drop_column("partner_staff", "last_name")
    op.drop_column("partner_staff", "first_name")
