"""add slug to hotels with backfill

Revision ID: add_hotel_slug
Revises: e7a75efd5fd7
Create Date: 2026-05-16 16:00:00.000000

"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_hotel_slug"
down_revision: Union[str, None] = "e7a75efd5fd7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s):
    if not s:
        return ""
    s = s.lower().strip()
    return _SLUG_RE.sub("-", s).strip("-")[:60]


def upgrade() -> None:
    op.add_column("hotels", sa.Column("slug", sa.String(length=128), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name_en FROM hotels ORDER BY id")).fetchall()
    used: set[str] = set()
    for hid, name_en in rows:
        base = _slugify(name_en) or f"hotel-{hid}"
        candidate = base
        n = 0
        while candidate in used:
            n += 1
            candidate = f"{base}-{n}"
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE hotels SET slug = :s WHERE id = :i"),
            {"s": candidate, "i": hid},
        )
    op.alter_column("hotels", "slug", nullable=False)
    op.create_index("ix_hotels_slug", "hotels", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_hotels_slug", table_name="hotels")
    op.drop_column("hotels", "slug")
