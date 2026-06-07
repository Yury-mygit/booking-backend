"""GC consumer-эндпоинт для media-сервиса.

media шлёт `GET /api/v1/media-refs` с заголовком
`X-Media-GC-Token: <settings.media_gc_token>` периодически (cron в
`media_dev_app` lifespan). В ответе — DISTINCT asset_id (UUID-строки),
на которые ссылается booking: `hotels.photos`, `rooms.photos`,
`clients.photo_url`.

media помечает assets, отсутствующие в объединении всех consumer-refs,
как orphan и удаляет в следующем GC проходе (см. `MEDIA_CONSUMERS` в
`media/.env`).

Legacy URL'ы (`/api/v1/photos/...` до Stage 5) **отфильтровываются по
UUID-регексу** — media их не отслеживает; включение в ответ привело бы
к шуму в логах.
"""
import re

from fastapi import APIRouter, Depends, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import APIError

router = APIRouter(tags=["media_refs"])

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


async def _require_gc_token(x_media_gc_token: str | None = Header(default=None)) -> None:
    expected = settings.media_gc_token
    if not expected:
        raise APIError(503, "media_gc_disabled", "MEDIA_GC_TOKEN is not configured")
    if x_media_gc_token != expected:
        raise APIError(401, "unauthorized", "Bad X-Media-GC-Token")


@router.get("/media-refs", dependencies=[Depends(_require_gc_token)])
async def list_media_refs(db: AsyncSession = Depends(get_db)) -> dict:
    sql = text(
        """
        SELECT DISTINCT asset_id FROM (
            SELECT jsonb_array_elements_text(photos) AS asset_id
            FROM hotels WHERE photos IS NOT NULL
            UNION
            SELECT jsonb_array_elements_text(photos)
            FROM rooms WHERE photos IS NOT NULL
            UNION
            SELECT photo_url
            FROM clients WHERE photo_url IS NOT NULL
        ) t
        WHERE asset_id IS NOT NULL
        """
    )
    rows = (await db.execute(sql)).all()
    refs = [r[0] for r in rows if r[0] and _UUID_RE.match(r[0])]
    return {"asset_ids": refs, "count": len(refs)}
