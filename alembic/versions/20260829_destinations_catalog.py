"""destinations catalog + hotels.destination_id + city normalize (TBB-68)

- Создаёт таблицу `destinations` и seed'ит 4 записи:
  Бишкек / Иссык-Куль / Нарын / Ош (все active).
- Нормализует `hotels.city`: латиница → кириллица (Bishkek→Бишкек,
  Cholpon-Ata→Чолпон-Ата, Karakol→Каракол, Naryn→Нарын, Osh→Ош).
- Добавляет `hotels.destination_id` (nullable), backfill'ит по маппингу
  city → destination, затем переводит в NOT NULL.

Backfill mapping:
  Бишкек        → bishkek
  Чолпон-Ата    → issyk-kul
  Каракол       → issyk-kul   (город Каракол принадлежит региону Иссык-Куль)
  Нарын         → naryn
  Ош            → osh

Все существующие 12 отелей попадают в один из 4 destination'ов —
после backfill'а нет NULL, миграция ставит NOT NULL в той же ревизии.

Revision ID: destinations_catalog
Revises: hotel_amenity_options
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "destinations_catalog"
down_revision: Union[str, None] = "hotel_amenity_options"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED = [
    # slug, name_ru, sort_order
    ("bishkek",   "Бишкек",     0),
    ("issyk-kul", "Иссык-Куль", 1),
    ("naryn",     "Нарын",      2),
    ("osh",       "Ош",         3),
]

# Латиница → кириллица + маппинг город→destination slug.
CITY_NORMALIZE = {
    "Bishkek":     ("Бишкек",     "bishkek"),
    "Бишкек":      ("Бишкек",     "bishkek"),
    "Cholpon-Ata": ("Чолпон-Ата", "issyk-kul"),
    "Karakol":     ("Каракол",    "issyk-kul"),
    "Naryn":       ("Нарын",      "naryn"),
    "Osh":         ("Ош",         "osh"),
}


def upgrade() -> None:
    op.create_table(
        "destinations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name_ru", sa.String(80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_destinations_active_sort",
        "destinations",
        ["active", "sort_order"],
    )

    conn = op.get_bind()
    for slug, name_ru, sort_order in SEED:
        conn.execute(
            sa.text(
                "INSERT INTO destinations (slug, name_ru, sort_order, active) "
                "VALUES (:slug, :name_ru, :sort_order, true)"
            ),
            {"slug": slug, "name_ru": name_ru, "sort_order": sort_order},
        )

    # Нормализация hotels.city (латиница → кириллица) — до создания FK,
    # чтобы backfill сматчил по кириллице.
    for old_city, (new_city, _slug) in CITY_NORMALIZE.items():
        if old_city == new_city:
            continue
        conn.execute(
            sa.text("UPDATE hotels SET city = :new WHERE city = :old"),
            {"old": old_city, "new": new_city},
        )

    op.add_column(
        "hotels",
        sa.Column(
            "destination_id",
            sa.Integer(),
            sa.ForeignKey("destinations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_hotels_destination_id", "hotels", ["destination_id"])

    # Backfill: для каждой (нормализованной) кириллической city → slug.
    for _old, (city_ru, slug) in CITY_NORMALIZE.items():
        conn.execute(
            sa.text(
                "UPDATE hotels SET destination_id = "
                "(SELECT id FROM destinations WHERE slug = :slug) "
                "WHERE city = :city AND destination_id IS NULL"
            ),
            {"slug": slug, "city": city_ru},
        )

    # Проверка: после backfill не должно остаться NULL.
    remaining = conn.execute(
        sa.text("SELECT COUNT(*) FROM hotels WHERE destination_id IS NULL")
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"TBB-68 backfill: {remaining} hotels остались без destination_id; "
            "добавь маппинг для их городов в CITY_NORMALIZE и повтори."
        )

    op.alter_column("hotels", "destination_id", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_hotels_destination_id", "hotels")
    op.drop_column("hotels", "destination_id")
    op.drop_index("ix_destinations_active_sort", "destinations")
    op.drop_table("destinations")
