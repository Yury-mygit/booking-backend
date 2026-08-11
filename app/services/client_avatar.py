"""TBB-53: sync Client.photo_url from Telegram initData.user.photo_url.

Fire-and-forget entrypoint `refresh_client_avatar_from_tg` — вызывается из
`api/auth.py:auth_tg` через `asyncio.create_task` после `db.commit()` когда
`Client.photo_url_source` расходится с новым `photo_url` из initData.

Свой AsyncSession — исходная сессия уже закрыта на момент task execution
(`feedback_async_sqlalchemy_post_commit`). Все ошибки логируем `print`'ом
без пробрасывания — auth-response уже отправлен.
"""
import httpx

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import Client
from app.services.media_client import upload_to_media

_TIMEOUT_SEC = 5.0
_MAX_BYTES = 2 * 1024 * 1024  # TG avatars — sub-MB, cap 2MB safety


async def refresh_client_avatar_from_tg(
    client_id: int,
    source_url: str,
    uploader_id: int,
) -> None:
    if not source_url:
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            r = await client.get(source_url)
        if r.status_code != 200:
            return
        data = r.content
        if not data or len(data) > _MAX_BYTES:
            return
        mime = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip()

        asset_id = await upload_to_media(data, mime, uploader_id=uploader_id)

        async with AsyncSessionLocal() as db:
            c = await db.get(Client, client_id)
            if c is None:
                return
            c.photo_url = asset_id
            c.photo_url_source = source_url
            await db.commit()
    except Exception as e:
        print(f"[client_avatar] refresh failed client={client_id}: {e!r}")
