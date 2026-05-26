"""Photo upload/serve для hotels/rooms/clients.

Storage layout: `{settings.storage_path}/{kind}/{id}/{token}.{ext}`.
URL шаблон: `/api/v1/photos/{kind}/{id}/{filename}`.

Все write-операции работают в scope'е `accessible_owners` (owner +
staff с правами на отель). До 2026-05-26 локальные `_get_my_*_or_404`
проверяли `owner_user_id == ctx.user.id`, что отрезало staff'у доступ —
теперь через `app.services.scope.*`.

Магический-байт sniff (`_MAGIC`) делает грубую проверку «это вообще
картинка», поверх MIME из `UploadFile`.
"""
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, require_role, require_verified_partner
from app.core.exceptions import APIError
from app.models.models import UserRole
from app.services import scope

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


def _safe_room_dir(room_id: int) -> Path:
    return Path(settings.storage_path) / "rooms" / str(room_id)


def _room_url(room_id: int, filename: str) -> str:
    return f"/api/v1/photos/rooms/{room_id}/{filename}"


@router.post("/p/hotels/{hotel_id}/photos")
async def upload_photo(
    hotel_id: int,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id)

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
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id)
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
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id)
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


# ─── Room photos ───────────────────────────────────────────────────────────

@router.post("/p/rooms/{room_id}/photos")
async def upload_room_photo(
    room_id: int,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id)

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

    dst_dir = _safe_room_dir(room_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    fname = secrets.token_urlsafe(12) + ext
    (dst_dir / fname).write_bytes(data)

    url = _room_url(room_id, fname)
    photos = list(r.photos or [])
    photos.append(url)
    r.photos = photos
    await db.commit()
    await db.refresh(r)
    return {"url": url, "photos": r.photos}


@router.delete("/p/rooms/{room_id}/photos", status_code=204)
async def delete_room_photo(
    room_id: int,
    url: str = Query(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id)
    photos = list(r.photos or [])
    if url not in photos:
        raise APIError(404, "not_found", "Photo not in room")
    photos.remove(url)
    r.photos = photos
    await db.commit()

    prefix = f"/api/v1/photos/rooms/{room_id}/"
    if url.startswith(prefix):
        fname = url[len(prefix):]
        if "/" not in fname and ".." not in fname:
            p = _safe_room_dir(room_id) / fname
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
    return None


@router.put("/p/rooms/{room_id}/photos/reorder")
async def reorder_room_photos(
    room_id: int,
    payload: PhotosReorder,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id)
    current = set(r.photos or [])
    new = set(payload.urls)
    if current != new:
        raise APIError(400, "bad_request", "Reorder must contain exactly the same URLs")
    r.photos = list(payload.urls)
    await db.commit()
    await db.refresh(r)
    return {"photos": r.photos}


@router.get("/photos/rooms/{room_id}/{filename}")
async def serve_room_photo(room_id: int, filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise APIError(400, "bad_request", "Invalid filename")
    p = _safe_room_dir(room_id) / filename
    if not p.exists() or not p.is_file():
        raise APIError(404, "not_found", "Photo not found")
    return FileResponse(p)


# ─── Client photos ─────────────────────────────────────────────────────────

def _safe_client_dir(client_id: int) -> Path:
    return Path(settings.storage_path) / "clients" / str(client_id)


def _client_url(client_id: int, filename: str) -> str:
    return f"/api/v1/photos/clients/{client_id}/{filename}"


@router.post("/p/clients/{client_id}/photo")
async def upload_client_photo(
    client_id: int,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)

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

    dst_dir = _safe_client_dir(client_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    fname = secrets.token_urlsafe(12) + ext
    (dst_dir / fname).write_bytes(data)

    # Replace previous photo (single-photo model for clients).
    prev = c.photo_url
    url = _client_url(client_id, fname)
    c.photo_url = url
    await db.commit()

    if prev and prev.startswith(f"/api/v1/photos/clients/{client_id}/"):
        old_fname = prev.rsplit("/", 1)[-1]
        if "/" not in old_fname and ".." not in old_fname:
            old_p = _safe_client_dir(client_id) / old_fname
            if old_p.exists():
                try:
                    old_p.unlink()
                except OSError:
                    pass
    return {"url": url}


@router.delete("/p/clients/{client_id}/photo", status_code=204)
async def delete_client_photo(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
    prev = c.photo_url
    c.photo_url = None
    await db.commit()
    if prev and prev.startswith(f"/api/v1/photos/clients/{client_id}/"):
        fname = prev.rsplit("/", 1)[-1]
        if "/" not in fname and ".." not in fname:
            p = _safe_client_dir(client_id) / fname
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
    return None


@router.get("/photos/clients/{client_id}/{filename}")
async def serve_client_photo(client_id: int, filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise APIError(400, "bad_request", "Invalid filename")
    p = _safe_client_dir(client_id) / filename
    if not p.exists() or not p.is_file():
        raise APIError(404, "not_found", "Photo not found")
    return FileResponse(p)
