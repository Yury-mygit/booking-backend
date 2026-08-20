"""hotel rules v2 — enum expand (BookingMode +2, CancelPolicy +2) — TBB-64

Расширяет 2 enum-типа `booking_mode` и `cancel_policy` через полное
пересоздание (postgres не поддерживает DROP VALUE — recreate = единственный
путь к обратимой миграции).

Изменения:
- `booking_mode`: renamed `with_confirmation` → `manual_confirmation`;
  added `phone_confirmation`, `advance_payment`.
- `cancel_policy`: added `non_refundable`, `first_night_only`.

Existing rows: 5 hotels все на default'ах (instant/free), 53 bookings
snapshot_* = NULL. CAST-cast работает pass-through.

Revision ID: hotel_rules_v2
Revises: hotel_booking_rules
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "hotel_rules_v2"
down_revision: Union[str, None] = "hotel_booking_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BM_V1 = ("instant", "with_confirmation")
BM_V2 = ("instant", "manual_confirmation", "phone_confirmation", "advance_payment")
CP_V1 = ("free", "hold_after_days")
CP_V2 = ("free", "hold_after_days", "non_refundable", "first_night_only")


def _recreate_enum_type(
    type_name: str,
    new_values: tuple[str, ...],
    columns: list[tuple[str, str]],  # [(table, column), …]
    cast_expr_by_column: dict[tuple[str, str], str],
    default_by_column: dict[tuple[str, str], str | None],
) -> None:
    """Пересоздать postgres enum type: CREATE _v2 → ALTER USING → DROP old → RENAME.

    `cast_expr_by_column[(table, col)]` — SQL-выражение для USING (обычно
    CASE ... END для переименований значений; для pass-through — просто
    `column::text::type_v2`).

    `default_by_column[(table, col)]` — server_default для восстановления
    после ALTER (иначе default сбрасывается при type-change).
    """
    tmp = f"{type_name}_v2"
    values_sql = ", ".join(f"'{v}'" for v in new_values)
    op.execute(f"CREATE TYPE {tmp} AS ENUM ({values_sql})")
    for (table, col) in columns:
        default = default_by_column.get((table, col))
        cast_expr = cast_expr_by_column[(table, col)]
        if default is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {col} DROP DEFAULT")
        # USING нужен explicit cast text → enum (postgres не auto-cast'ит).
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {tmp} USING (({cast_expr})::{tmp})")
        if default is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT '{default}'::{tmp}")
    op.execute(f"DROP TYPE {type_name}")
    op.execute(f"ALTER TYPE {tmp} RENAME TO {type_name}")


def upgrade() -> None:
    # booking_mode: rename with_confirmation → manual_confirmation.
    _recreate_enum_type(
        type_name="booking_mode",
        new_values=BM_V2,
        columns=[("hotels", "booking_mode"), ("bookings", "snapshot_booking_mode")],
        cast_expr_by_column={
            ("hotels", "booking_mode"):
                "CASE WHEN booking_mode::text = 'with_confirmation' "
                "THEN 'manual_confirmation' ELSE booking_mode::text END",
            ("bookings", "snapshot_booking_mode"):
                "CASE WHEN snapshot_booking_mode::text = 'with_confirmation' "
                "THEN 'manual_confirmation' ELSE snapshot_booking_mode::text END",
        },
        default_by_column={
            ("hotels", "booking_mode"): "instant",
            ("bookings", "snapshot_booking_mode"): None,
        },
    )
    # cancel_policy: pass-through cast (только добавили значения).
    _recreate_enum_type(
        type_name="cancel_policy",
        new_values=CP_V2,
        columns=[("hotels", "cancel_policy"), ("bookings", "snapshot_cancel_policy")],
        cast_expr_by_column={
            ("hotels", "cancel_policy"): "cancel_policy::text",
            ("bookings", "snapshot_cancel_policy"): "snapshot_cancel_policy::text",
        },
        default_by_column={
            ("hotels", "cancel_policy"): "free",
            ("bookings", "snapshot_cancel_policy"): None,
        },
    )


def downgrade() -> None:
    # cancel_policy: recreate v1. Существующие rows с non_refundable /
    # first_night_only будут отвергнуты CAST'ом — обрабатываем через
    # fallback в free (безопасно семантически).
    _recreate_enum_type(
        type_name="cancel_policy",
        new_values=CP_V1,
        columns=[("hotels", "cancel_policy"), ("bookings", "snapshot_cancel_policy")],
        cast_expr_by_column={
            ("hotels", "cancel_policy"):
                "CASE WHEN cancel_policy::text IN ('non_refundable', 'first_night_only') "
                "THEN 'free' ELSE cancel_policy::text END",
            ("bookings", "snapshot_cancel_policy"):
                "CASE WHEN snapshot_cancel_policy::text IN ('non_refundable', 'first_night_only') "
                "THEN 'free' ELSE snapshot_cancel_policy::text END",
        },
        default_by_column={
            ("hotels", "cancel_policy"): "free",
            ("bookings", "snapshot_cancel_policy"): None,
        },
    )
    # booking_mode: recreate v1 с обратным rename.
    _recreate_enum_type(
        type_name="booking_mode",
        new_values=BM_V1,
        columns=[("hotels", "booking_mode"), ("bookings", "snapshot_booking_mode")],
        cast_expr_by_column={
            ("hotels", "booking_mode"):
                "CASE WHEN booking_mode::text = 'manual_confirmation' "
                "THEN 'with_confirmation' "
                "WHEN booking_mode::text IN ('phone_confirmation', 'advance_payment') "
                "THEN 'instant' ELSE booking_mode::text END",
            ("bookings", "snapshot_booking_mode"):
                "CASE WHEN snapshot_booking_mode::text = 'manual_confirmation' "
                "THEN 'with_confirmation' "
                "WHEN snapshot_booking_mode::text IN ('phone_confirmation', 'advance_payment') "
                "THEN 'instant' ELSE snapshot_booking_mode::text END",
        },
        default_by_column={
            ("hotels", "booking_mode"): "instant",
            ("bookings", "snapshot_booking_mode"): None,
        },
    )
