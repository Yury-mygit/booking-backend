"""Сериализация значений `photos: list[str]` / `photo_url` для UI.

DB хранит:
- для НОВЫХ фото (после Stage 2 миграции) — `asset_id` (UUID-строкой);
- для СТАРЫХ (до Stage 5 bulk migration) — legacy URL вроде
  `/api/v1/photos/hotels/3/abc.jpg`.

Фронт получает обе формы как URL: UUID → абсолют
`{media_public_base}/api/v1/assets/{uuid}`; legacy — как есть.

Для INPUT (delete/reorder query/body) — обратная нормализация: URL
media-asset'а превращаем в UUID, чтобы сравнить с DB.
"""

import re
from uuid import UUID

from app.core.config import settings

_ASSET_URL_RE = re.compile(
    r"^https?://[^/]+/api/v1/assets/([0-9a-f-]{36})(?:/thumb)?/?$",
    re.IGNORECASE,
)


def is_asset_id(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def to_response_url(value: str | None) -> str | None:
    """DB-значение → URL для фронта.
    UUID → media public URL; legacy URL — как есть; None/'' → как есть."""
    if not value:
        return value
    if is_asset_id(value):
        return f"{settings.media_public_base}/api/v1/assets/{value}"
    return value


def to_response_urls(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return [to_response_url(v) for v in values if v]


def normalize_input(value: str | None) -> str | None:
    """Frontend-значение → DB-форма для матчинга в delete/reorder.
    Публичный media URL → UUID; всё остальное — как есть."""
    if not value:
        return value
    m = _ASSET_URL_RE.match(value)
    if m:
        return m.group(1)
    return value
