"""rooms.beds → single_beds/double_beds

Карта: open_cards/cards/booking/refactor/2026-05-31-room-beds-meals-guests.md
(Этап 1.1). Старая колонка rooms.beds дропается; данные переезжают в
single_beds (single_beds = COALESCE(beds, 0)). double_beds = 0 —
партнёр доуточняет вручную через partner SPA. Backfill реальных
конфигураций по эвристике (capacity + name) выполнен отдельным SQL
после миграции (см. этап 5a карты).

Revision ID: room_beds_split
Revises: users_bot_blocked
Create Date: 2026-05-31 06:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "room_beds_split"
down_revision: Union[str, None] = "users_bot_blocked"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rooms",
        sa.Column("single_beds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rooms",
        sa.Column("double_beds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE rooms SET single_beds = COALESCE(beds, 0)")
    op.drop_column("rooms", "beds")


def downgrade() -> None:
    op.add_column("rooms", sa.Column("beds", sa.Integer(), nullable=True))
    op.execute("UPDATE rooms SET beds = single_beds")
    op.drop_column("rooms", "double_beds")
    op.drop_column("rooms", "single_beds")
