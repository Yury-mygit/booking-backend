"""Photo upload для hotels/rooms/clients — через media-сервис.

Карта: `cards/booking/feature/2026-05-27-booking-media-migration.md`,
Stages 2 + 7.

После Stage 5 миграции (2026-06-07) в DB хранятся только
`asset_id` (UUID-строкой); legacy URL'ы `/api/v1/photos/...` и
обслуживающие их endpoint'ы удалены (Stage 7). Фронт получает
`{media_public_base}/api/v1/assets/{uuid}` через сериализатор
`app.services.photo_format.to_response_url`.

Магический-байт sniff (`_MAGIC`) делает грубую проверку «это вообще
картинка», поверх MIME из `UploadFile` (defense in depth — media сам
валидирует через Pillow.verify).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.services import scope
from app.services.media_client import to_public_url, upload_to_media
from app.services.photo_format import normalize_input, to_response_urls

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
# Rough magic-byte sniffs to make sure the upload is actually an image.
_MAGIC = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"RIFF",  # WEBP (RIFF...WEBP)
)


router = APIRouter(tags=["photos"])


class PhotosReorder(BaseModel):
    urls: list[str]


async def _validate_image_upload(file: UploadFile) -> tuple[bytes, str]:
    """Проверка ext + size + magic. Возвращает (bytes, mime)."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise APIError(400, "bad_format", "Allowed: jpg, jpeg, png, webp")
    data = await file.read()
    if len(data) == 0:
        raise APIError(400, "empty", "Empty file")
    if len(data) > settings.photo_max_bytes:
        raise APIError(400, "too_large", f"Max {settings.photo_max_bytes} bytes")
    if not any(data.startswith(m) for m in _MAGIC):
        raise APIError(400, "bad_format", "Not a valid image")
    return data, _MIME_BY_EXT[ext]


# ─── Hotel photos ──────────────────────────────────────────────────────────

@router.post("/p/hotels/{hotel_id}/photos")
async def upload_photo(
    hotel_id: int,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id)
    data, mime = await _validate_image_upload(file)
    asset_id = await upload_to_media(data, mime, uploader_id=ctx.user.id)

    photos = list(h.photos or [])
    photos.append(asset_id)
    h.photos = photos
    await db.commit()
    await db.refresh(h)
    return {"url": to_public_url(asset_id), "photos": to_response_urls(h.photos)}


@router.delete("/p/hotels/{hotel_id}/photos", status_code=204)
async def delete_photo(
    hotel_id: int,
    url: str = Query(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id)
    photos = list(h.photos or [])
    target = normalize_input(url)
    idx = next(
        (i for i, p in enumerate(photos) if normalize_input(p) == target),
        None,
    )
    if idx is None:
        raise APIError(404, "not_found", "Photo not in hotel")
    photos.pop(idx)
    h.photos = photos
    await db.commit()
    # Физически файл чистит media GC через `/api/v1/media-refs`.
    return None


@router.put("/p/hotels/{hotel_id}/photos/reorder")
async def reorder_photos(
    hotel_id: int,
    payload: PhotosReorder,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id)
    current_norm = {normalize_input(p) for p in (h.photos or [])}
    input_norm = [normalize_input(u) for u in payload.urls]
    if current_norm != set(input_norm):
        raise APIError(
            400,
            "bad_request",
            "Reorder must contain exactly the same photos as currently saved",
        )
    h.photos = input_norm
    await db.commit()
    await db.refresh(h)
    return {"photos": to_response_urls(h.photos)}


# ─── Room photos ───────────────────────────────────────────────────────────

@router.post("/p/rooms/{room_id}/photos")
async def upload_room_photo(
    room_id: int,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id)
    data, mime = await _validate_image_upload(file)
    asset_id = await upload_to_media(data, mime, uploader_id=ctx.user.id)

    photos = list(r.photos or [])
    photos.append(asset_id)
    r.photos = photos
    await db.commit()
    await db.refresh(r)
    return {"url": to_public_url(asset_id), "photos": to_response_urls(r.photos)}


@router.delete("/p/rooms/{room_id}/photos", status_code=204)
async def delete_room_photo(
    room_id: int,
    url: str = Query(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id)
    photos = list(r.photos or [])
    target = normalize_input(url)
    idx = next(
        (i for i, p in enumerate(photos) if normalize_input(p) == target),
        None,
    )
    if idx is None:
        raise APIError(404, "not_found", "Photo not in room")
    photos.pop(idx)
    r.photos = photos
    await db.commit()
    return None


@router.put("/p/rooms/{room_id}/photos/reorder")
async def reorder_room_photos(
    room_id: int,
    payload: PhotosReorder,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id)
    current_norm = {normalize_input(p) for p in (r.photos or [])}
    input_norm = [normalize_input(u) for u in payload.urls]
    if current_norm != set(input_norm):
        raise APIError(400, "bad_request", "Reorder must contain exactly the same photos")
    r.photos = input_norm
    await db.commit()
    await db.refresh(r)
    return {"photos": to_response_urls(r.photos)}


# ─── Client photos ─────────────────────────────────────────────────────────

@router.post("/p/clients/{client_id}/photo")
async def upload_client_photo(
    client_id: int,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
    data, mime = await _validate_image_upload(file)
    asset_id = await upload_to_media(data, mime, uploader_id=ctx.user.id)

    # Single-photo model для clients. Колонка называется photo_url по
    # историческим причинам, хранит теперь asset_id.
    c.photo_url = asset_id
    await db.commit()
    return {"url": to_public_url(asset_id)}


@router.delete("/p/clients/{client_id}/photo", status_code=204)
async def delete_client_photo(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
    c.photo_url = None
    await db.commit()
    return None
