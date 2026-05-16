"""Photo upload/serve for hotels.

Storage layout:
    {settings.storage_path}/hotels/{hotel_id}/{token}.{ext}

URL: /api/v1/photos/hotels/{hotel_id}/{filename}
"""
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, require_role
from app.core.exceptions import APIError
from app.models.models import UserRole

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
# Rough magic-byte sniffs to make sure the upload is actually an image.
_MAGIC = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"RIFF",  # WEBP (RIFF...WEBP)
)


router = APIRouter(tags=["photos"])


class PhotosReorder(BaseModel):
    urls: list[str]


def _safe_hotel_dir(hotel_id: int) -> Path:
    return Path(settings.storage_path) / "hotels" / str(hotel_id)


def _url_for(hotel_id: int, filename: str) -> str:
    return f"/api/v1/photos/hotels/{hotel_id}/{filename}"


async def _get_my_hotel_or_404(db: AsyncSession, ctx: AuthContext, hotel_id: int):
    # Local helper to avoid circular import on partner module.
    from app.models.models import Hotel
    from sqlalchemy import select
    h = (
        await db.execute(
            select(Hotel).where(Hotel.id == hotel_id, Hotel.owner_user_id == ctx.user.id)
        )
    ).scalar_one_or_none()
    if h is None:
        raise APIError(404, "not_found", "Hotel not found")
    return h


@router.post("/p/hotels/{hotel_id}/photos")
async def upload_photo(
    hotel_id: int,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel_or_404(db, ctx, hotel_id)

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

    dst_dir = _safe_hotel_dir(hotel_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    fname = secrets.token_urlsafe(12) + ext
    (dst_dir / fname).write_bytes(data)

    url = _url_for(hotel_id, fname)
    photos = list(h.photos or [])
    photos.append(url)
    h.photos = photos
    await db.commit()
    await db.refresh(h)
    return {"url": url, "photos": h.photos}


@router.delete("/p/hotels/{hotel_id}/photos", status_code=204)
async def delete_photo(
    hotel_id: int,
    url: str = Query(...),
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel_or_404(db, ctx, hotel_id)
    photos = list(h.photos or [])
    if url not in photos:
        raise APIError(404, "not_found", "Photo not in hotel")
    photos.remove(url)
    h.photos = photos
    await db.commit()

    # Best-effort disk cleanup (only if it's our managed path).
    prefix = f"/api/v1/photos/hotels/{hotel_id}/"
    if url.startswith(prefix):
        fname = url[len(prefix):]
        if "/" not in fname and ".." not in fname:
            p = _safe_hotel_dir(hotel_id) / fname
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
    return None


@router.put("/p/hotels/{hotel_id}/photos/reorder")
async def reorder_photos(
    hotel_id: int,
    payload: PhotosReorder,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel_or_404(db, ctx, hotel_id)
    current = set(h.photos or [])
    new = set(payload.urls)
    if current != new:
        raise APIError(
            400,
            "bad_request",
            "Reorder must contain exactly the same URLs as currently saved",
        )
    h.photos = list(payload.urls)
    await db.commit()
    await db.refresh(h)
    return {"photos": h.photos}


@router.get("/photos/hotels/{hotel_id}/{filename}")
async def serve_photo(hotel_id: int, filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise APIError(400, "bad_request", "Invalid filename")
    p = _safe_hotel_dir(hotel_id) / filename
    if not p.exists() or not p.is_file():
        raise APIError(404, "not_found", "Photo not found")
    return FileResponse(p)
