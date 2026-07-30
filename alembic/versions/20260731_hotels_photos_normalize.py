"""hotels.photos — нормализация legacy media URL → чистый UUID + очистка фейков

TBB-35: `api/media_refs.py` фильтрует список referenced asset_id строгим
UUID-regex; hotels.photos у отеля id=7 (Ala-Too) хранились как полные media
URL — не попадали в /media-refs → media GC пометил как orphan и физически
удалил файлы. Дополнительно id=1 (Demo) и id=3 (Teskey) содержат фейковые
URL к чужим сервисам (example.com / imgur.com).

Одна ревизия приводит `hotels.photos` к единому формату UUID:
  * MEDIA_URL (`https://…/api/v1/assets/<uuid>`) → извлечь UUID.
  * UUID → как есть.
  * OTHER (фейки example.com / imgur.com) → отфильтровать (удалить элемент).

Rooms/clients уже чисты (аудит 2026-07-30) — не трогаем.

Downgrade — no-op: обратная операция (восстановление legacy URL из UUID)
невозможна и не нужна (см. decision_md D1).

Revision ID: hotels_photos_normalize
Revises: booking_source
Create Date: 2026-07-31
"""
import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "hotels_photos_normalize"
down_revision: Union[str, None] = "booking_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ASSET_URL_RE = re.compile(
    r"^https?://[^/]+/api/v1/assets/([0-9a-f-]{36})(?:/thumb)?/?$",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _normalize(items: list[str]) -> list[str]:
    out: list[str] = []
    for it in items or []:
        if not isinstance(it, str):
            continue
        if _UUID_RE.match(it):
            out.append(it)
            continue
        m = _ASSET_URL_RE.match(it)
        if m:
            out.append(m.group(1))
        # else: OTHER (фейковые внешние URL) — drop
    return out


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, photos FROM hotels "
            "WHERE photos IS NOT NULL AND jsonb_array_length(photos::jsonb) > 0"
        )
    ).all()
    for row in rows:
        original = list(row.photos)
        new = _normalize(original)
        if new != original:
            conn.execute(
                sa.text("UPDATE hotels SET photos = CAST(:p AS jsonb) WHERE id = :id"),
                {"p": json.dumps(new), "id": row.id},
            )


def downgrade() -> None:
    pass
