"""hotel booking rules (min_stay + booking_mode + cancel_policy) — TBB-62

Foundation-миграция для эпика TBB-61 «Правила отеля». Добавляет:
- 2 postgres-enum type'а: `booking_mode`, `cancel_policy`.
- 5 полей в `hotels` (NOT NULL с server_default'ами для существующих
  строк: 1 ночь, instant, free — не меняет поведение).
- 5 полей в `bookings` как snapshot (NULLable, семантика: NULL = default
  правила на момент создания брони; для существующих броней backfill
  не делаем).

Revision ID: hotel_booking_rules
Revises: client_photo_url_source
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "hotel_booking_rules"
down_revision: Union[str, None] = "client_photo_url_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BOOKING_MODE_VALUES = ("instant", "with_confirmation")
CANCEL_POLICY_VALUES = ("free", "hold_after_days")


def upgrade() -> None:
    booking_mode = sa.Enum(*BOOKING_MODE_VALUES, name="booking_mode")
    cancel_policy = sa.Enum(*CANCEL_POLICY_VALUES, name="cancel_policy")
    booking_mode.create(op.get_bind(), checkfirst=True)
    cancel_policy.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "hotels",
        sa.Column(
            "min_stay_nights",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "hotels",
        sa.Column(
            "booking_mode",
            sa.Enum(*BOOKING_MODE_VALUES, name="booking_mode", create_type=False),
            server_default="instant",
            nullable=False,
        ),
    )
    op.add_column(
        "hotels",
        sa.Column(
            "cancel_policy",
            sa.Enum(*CANCEL_POLICY_VALUES, name="cancel_policy", create_type=False),
            server_default="free",
            nullable=False,
        ),
    )
    op.add_column("hotels", sa.Column("cancel_days_threshold", sa.Integer(), nullable=True))
    op.add_column("hotels", sa.Column("cancel_penalty_pct", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_hotels_cancel_penalty_pct_range",
        "hotels",
        "cancel_penalty_pct IS NULL OR (cancel_penalty_pct BETWEEN 0 AND 100)",
    )

    op.add_column("bookings", sa.Column("snapshot_min_stay_nights", sa.Integer(), nullable=True))
    op.add_column(
        "bookings",
        sa.Column(
            "snapshot_booking_mode",
            sa.Enum(*BOOKING_MODE_VALUES, name="booking_mode", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "snapshot_cancel_policy",
            sa.Enum(*CANCEL_POLICY_VALUES, name="cancel_policy", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("bookings", sa.Column("snapshot_cancel_days_threshold", sa.Integer(), nullable=True))
    op.add_column("bookings", sa.Column("snapshot_cancel_penalty_pct", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("bookings", "snapshot_cancel_penalty_pct")
    op.drop_column("bookings", "snapshot_cancel_days_threshold")
    op.drop_column("bookings", "snapshot_cancel_policy")
    op.drop_column("bookings", "snapshot_booking_mode")
    op.drop_column("bookings", "snapshot_min_stay_nights")

    op.drop_constraint("ck_hotels_cancel_penalty_pct_range", "hotels", type_="check")
    op.drop_column("hotels", "cancel_penalty_pct")
    op.drop_column("hotels", "cancel_days_threshold")
    op.drop_column("hotels", "cancel_policy")
    op.drop_column("hotels", "booking_mode")
    op.drop_column("hotels", "min_stay_nights")

    sa.Enum(name="cancel_policy").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="booking_mode").drop(op.get_bind(), checkfirst=True)
