"""hotel_amenity_options — динамический каталог удобств (TBB-65 Stage 1)

Создаёт таблицу и seed'ит 12 существующих kind'ов (8 general + 4 dining)
с описаниями из frontend `locales/ru.json`. Новые варианты, созданные
админом, появляются с `active=false` и включаются вручную; seed'ы
активны сразу — миграция не должна менять поведение партнёра.

`HotelAmenity` enum в моделях остаётся (используется как whitelist в
pydantic до Stage 2); значения enum'а совпадают со slug'ами каталога.

Revision ID: hotel_amenity_options
Revises: hotel_rules_v2
Create Date: 2026-08-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "hotel_amenity_options"
down_revision: Union[str, None] = "hotel_rules_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED = [
    # section, slug, name, description
    ("general", "atm",              "Банкомат",                            "Банкомат"),
    ("general", "reception_24h",    "Круглосуточная стойка регистрации",   "Круглосуточная стойка регистрации"),
    ("general", "elevator",         "Лифт",                                "Лифт"),
    ("general", "press",            "Пресса",                              "Пресса"),
    ("general", "express_checkin",  "Ускоренная регистрация",              "Ускоренная регистрация заезда и выезда"),
    ("general", "wifi",             "Wi-Fi",                               "Wi-Fi"),
    ("general", "parking",          "Парковка",                            "Парковка"),
    ("general", "heating",          "Отопление",                           "Отопление"),
    ("dining",  "bar",              "Бар",                                 "Бар"),
    ("dining",  "free_tea_coffee",  "Бесплатный чай/кофе",                 "Бесплатный чай/кофе"),
    ("dining",  "breakfast",        "Завтрак",                             "Завтрак"),
    ("dining",  "restaurant",       "Ресторан",                            "Ресторан"),
]


def upgrade() -> None:
    op.create_table(
        "hotel_amenity_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("section", sa.String(16), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hotel_amenity_options_section_sort",
        "hotel_amenity_options",
        ["section", "sort_order"],
    )

    conn = op.get_bind()
    # per-section sort_order = index; seed'ы сразу active.
    per_section_idx = {"general": 0, "dining": 0}
    for section, slug, name, description in SEED:
        idx = per_section_idx[section]
        per_section_idx[section] += 1
        conn.execute(
            sa.text(
                "INSERT INTO hotel_amenity_options "
                "(section, slug, name, description, active, sort_order) "
                "VALUES (:section, :slug, :name, :description, true, :sort_order)"
            ),
            {"section": section, "slug": slug, "name": name,
             "description": description, "sort_order": idx},
        )


def downgrade() -> None:
    op.drop_index("ix_hotel_amenity_options_section_sort", "hotel_amenity_options")
    op.drop_table("hotel_amenity_options")
