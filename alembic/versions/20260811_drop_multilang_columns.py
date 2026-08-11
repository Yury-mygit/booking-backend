"""Drop multi-lang columns — TBB-52 (stakeholder: RU-only).

Scope: hotels, rooms, hotel_services — drop name_ky/name_en/description_ky/en.
(support_categories was created by 20260602 support_ticketing and dropped by
20260612 support_simplify_to_chat, so no cleanup needed here.)

Data loss: text in *_ky / *_en columns is discarded. Per stakeholder OK.

Existing hotel slugs preserved (not recomputed). New hotels compute slug via
transliterate(name_ru) → slugify_ru() in app.utils.

Revision ID: drop_multilang_columns
Revises: updated_at_core_mutable
Create Date: 2026-08-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "drop_multilang_columns"
down_revision: Union[str, None] = "updated_at_core_mutable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for tbl in ("hotels", "rooms"):
        op.drop_column(tbl, "name_ky")
        op.drop_column(tbl, "name_en")
        op.drop_column(tbl, "description_ky")
        op.drop_column(tbl, "description_en")

    op.drop_column("hotel_services", "name_ky")
    op.drop_column("hotel_services", "name_en")


def downgrade() -> None:
    for tbl in ("hotels", "rooms"):
        op.add_column(tbl, sa.Column("name_ky", sa.String(length=256), nullable=True))
        op.add_column(tbl, sa.Column("name_en", sa.String(length=256), nullable=True))
        op.add_column(tbl, sa.Column("description_ky", sa.Text(), nullable=True))
        op.add_column(tbl, sa.Column("description_en", sa.Text(), nullable=True))

    op.add_column("hotel_services", sa.Column("name_ky", sa.String(length=256), nullable=True))
    op.add_column("hotel_services", sa.Column("name_en", sa.String(length=256), nullable=True))
