"""hotels.meals (enum: none/breakfast/full_board)

Карта: open_cards/cards/booking/refactor/2026-05-31-room-beds-meals-guests.md
(Этап 1.5 — расширение). Заменяет первоначальную идею has_meals: bool
на 3-значный enum, чтобы различать «завтрак» и «полный пансион».
Бывший has_meals (которого в БД нет — выпиливался в той же сессии
до коммита) → enum meals со значением по умолчанию 'none'.

Revision ID: hotel_meals_kind
Revises: room_beds_split
Create Date: 2026-05-31 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "hotel_meals_kind"
down_revision: Union[str, None] = "room_beds_split"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


meals_kind = ENUM("none", "breakfast", "full_board", name="meals_kind", create_type=False)


def upgrade() -> None:
    meals_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "hotels",
        sa.Column(
            "meals",
            meals_kind,
            nullable=False,
            server_default="none",
        ),
    )


def downgrade() -> None:
    op.drop_column("hotels", "meals")
    meals_kind.drop(op.get_bind(), checkfirst=True)
