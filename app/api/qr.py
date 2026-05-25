"""Per-user payment QR — upload/serve/delete.

Storage layout:
    {settings.storage_path}/qr/{user_id}/{token}.{ext}

URL: /api/v1/qr/{user_id}/{filename}

Один QR на юзера. POST перезаписывает (старый файл удаляется).
GET публичный (QR показывается клиентам при оплате — приватность не нужна).
"""
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, current_user
from app.core.exceptions import APIError

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_MAGIC = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"RIFF",  # WEBP (RIFF....WEBP)
)


router = APIRouter(tags=["qr"])


def _qr_dir(user_id: int) -> Path:
    return Path(settings.storage_path) / "qr" / str(user_id)


def _qr_url(user_id: int, filename: str) -> str:
    return f"/api/v1/qr/{user_id}/{filename}"


def _delete_qr_file(user_id: int, url: str | None) -> None:
    if not url:
        return
    prefix = f"/api/v1/qr/{user_id}/"
    if not url.startswith(prefix):
        return
    fname = url[len(prefix):]
    if "/" in fname or ".." in fname:
        return
    p = _qr_dir(user_id) / fname
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


@router.get("/me/qr")
async def get_my_qr(ctx: AuthContext = Depends(current_user)):
    return {"url": ctx.user.qr_image_url}


@router.post("/me/qr")
async def upload_my_qr(
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
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

    user_id = ctx.user.id
    dst_dir = _qr_dir(user_id)
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Старый файл удалим, чтобы не накапливать orphan'ов при replace.
    _delete_qr_file(user_id, ctx.user.qr_image_url)

    fname = secrets.token_urlsafe(12) + ext
    (dst_dir / fname).write_bytes(data)

    url = _qr_url(user_id, fname)
    ctx.user.qr_image_url = url
    await db.commit()
    return {"url": url}


@router.delete("/me/qr", status_code=204)
async def delete_my_qr(
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    _delete_qr_file(ctx.user.id, ctx.user.qr_image_url)
    ctx.user.qr_image_url = None
    await db.commit()
    return None


@router.get("/qr/{user_id}/{filename}")
async def serve_qr(user_id: int, filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise APIError(400, "bad_request", "Invalid filename")
    p = _qr_dir(user_id) / filename
    if not p.exists() or not p.is_file():
        raise APIError(404, "not_found", "QR not found")
    return FileResponse(p)
