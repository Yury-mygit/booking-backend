"""HTTP-клиент для media-сервиса (server-to-server, без Caddy).

booking — самостоятельный продукт с собственной `users` таблицей
(TG-only пользователи без email), поэтому шлём в media surrogate
identity: `X-Uploader-System: booking, X-Uploader-Id: <users.id>`.
media пишет в `Asset.uploaded_by` композит `"booking:<id>"`.
"""

import httpx

from app.core.config import settings
from app.core.exceptions import APIError

UPLOAD_TIMEOUT_SEC = 30.0


async def upload_to_media(file_bytes: bytes, mime: str, *, uploader_id: int) -> str:
    """POST multipart в media и вернуть asset_id (UUID-строкой).

    Raises APIError при non-2xx — пробрасываем код media наружу (415, 401, 5xx)."""
    url = f"{settings.media_internal_url}/api/v1/assets"
    headers = {
        "X-Uploader-System": "booking",
        "X-Uploader-Id": str(uploader_id),
    }
    files = {"file": ("upload.bin", file_bytes, mime)}
    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SEC) as client:
        resp = await client.post(url, headers=headers, files=files)
    if resp.status_code >= 400:
        raise APIError(
            resp.status_code,
            "media_upload_failed",
            f"media POST /assets → {resp.status_code}: {resp.text[:200]}",
        )
    data = resp.json()
    asset_id = data.get("id")
    if not asset_id:
        raise APIError(502, "media_bad_response", "media response missing 'id'")
    return asset_id


def to_public_url(asset_id: str) -> str:
    """Композим публичный URL из asset_id для возврата фронту."""
    return f"{settings.media_public_base}/api/v1/assets/{asset_id}"


def to_public_thumb_url(asset_id: str) -> str:
    """Публичный URL миниатюры 256×256 webp."""
    return f"{settings.media_public_base}/api/v1/assets/{asset_id}/thumb"
