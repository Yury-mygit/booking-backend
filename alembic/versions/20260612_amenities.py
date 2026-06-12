"""hotels + rooms amenities (JSONB) and hotel checkin/checkout time.

Карта: cards/booking/feature/2026-06-12-amenities.md.

- hotels.amenities       JSONB NOT NULL DEFAULT '[]'
- hotels.checkin_time    TIME NULL
- hotels.checkout_time   TIME NULL
- rooms.amenities        JSONB NOT NULL DEFAULT '[]'

Enums HotelAmenity/RoomAmenity живут в Python-коде (Pydantic-валидация),
в БД хранятся как list[str] / list[{kind, paid?}] в JSONB. Тип PG enum
не создаём, чтобы добавление нового удобства не требовало DDL.

Revision ID: amenities
Revises: support_simplify_to_chat
Create Date: 2026-06-12 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "amenities"
down_revision: Union[str, None] = "support_simplify_to_chat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hotels",
        sa.Column(
            "amenities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )
    op.add_column("hotels", sa.Column("checkin_time", sa.Time(timezone=False), nullable=True))
    op.add_column("hotels", sa.Column("checkout_time", sa.Time(timezone=False), nullable=True))
    op.add_column(
        "rooms",
        sa.Column(
            "amenities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )


def downgrade() -> None:
    op.drop_column("rooms", "amenities")
    op.drop_column("hotels", "checkout_time")
    op.drop_column("hotels", "checkin_time")
    op.drop_column("hotels", "amenities")
