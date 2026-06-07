"""Bulk-миграция legacy photo URL → media asset_id.

Карта: `cards/booking/feature/2026-05-27-booking-media-migration.md`,
Stage 5.

Идемпотентно — значения, уже выглядящие как UUID, скипаются. Скрипт
можно запускать несколько раз; повторно ничего не дублирует (media
dedup'ит по sha256).

Использование внутри контейнера:

    docker exec booking_dev_app python scripts/migrate_photos_to_media.py --dry-run
    docker exec booking_dev_app python scripts/migrate_photos_to_media.py

uploaded_by для мигрированных файлов — `migration:booking-2026-06-07`
(чётко отличается от обычного `booking:<user_id>`).
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import Client, Hotel, Room

MIGRATION_SYSTEM = "migration"
MIGRATION_ID = "booking-2026-06-07"
LEGACY_URL_RE = re.compile(r"^/api/v1/photos/(hotels|rooms|clients)/(\d+)/(.+)$")
_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False


async def upload_one(client: httpx.AsyncClient, file_bytes: bytes, mime: str) -> str:
    resp = await client.post(
        f"{settings.media_internal_url}/api/v1/assets",
        headers={
            "X-Uploader-System": MIGRATION_SYSTEM,
            "X-Uploader-Id": MIGRATION_ID,
        },
        files={"file": ("migrated.bin", file_bytes, mime)},
    )
    resp.raise_for_status()
    return resp.json()["id"]


async def migrate_value(
    http: httpx.AsyncClient,
    value: str,
    storage_root: Path,
    stats: dict,
    dry_run: bool,
) -> str | None:
    """Вернуть новое значение для записи в DB, либо None (оставить как есть)."""
    if not value:
        return None
    if is_uuid(value):
        stats["already_uuid"] += 1
        return None
    m = LEGACY_URL_RE.match(value)
    if not m:
        stats["unknown_form"] += 1
        return None
    kind, entity_id, filename = m.groups()
    file_path = storage_root / kind / entity_id / filename
    if not file_path.exists():
        stats["file_missing"] += 1
        print(f"  MISSING: {file_path}", file=sys.stderr)
        return None
    mime = _MIME.get(Path(filename).suffix.lower(), "application/octet-stream")
    if dry_run:
        stats["would_migrate"] += 1
        print(f"  DRY: {value} ({file_path.stat().st_size} bytes, {mime})")
        return None
    data = file_path.read_bytes()
    asset_id = await upload_one(http, data, mime)
    stats["migrated"] += 1
    print(f"  OK: {value} → {asset_id}")
    return asset_id


async def main(dry_run: bool) -> None:
    storage_root = Path(settings.storage_path)
    stats = {
        "hotels_rows_touched": 0,
        "rooms_rows_touched": 0,
        "clients_rows_touched": 0,
        "migrated": 0,
        "would_migrate": 0,
        "already_uuid": 0,
        "unknown_form": 0,
        "file_missing": 0,
    }

    async with httpx.AsyncClient(timeout=60) as http:
        async with AsyncSessionLocal() as db:
            # Hotels
            print("== HOTELS ==")
            hotels = (
                await db.execute(select(Hotel).where(Hotel.photos.isnot(None)))
            ).scalars().all()
            for h in hotels:
                old = list(h.photos or [])
                if not old:
                    continue
                new_list = list(old)
                changed = False
                for i, v in enumerate(old):
                    new_v = await migrate_value(http, v, storage_root, stats, dry_run)
                    if new_v is not None:
                        new_list[i] = new_v
                        changed = True
                if changed:
                    h.photos = new_list
                    stats["hotels_rows_touched"] += 1

            # Rooms
            print("== ROOMS ==")
            rooms = (
                await db.execute(select(Room).where(Room.photos.isnot(None)))
            ).scalars().all()
            for r in rooms:
                old = list(r.photos or [])
                if not old:
                    continue
                new_list = list(old)
                changed = False
                for i, v in enumerate(old):
                    new_v = await migrate_value(http, v, storage_root, stats, dry_run)
                    if new_v is not None:
                        new_list[i] = new_v
                        changed = True
                if changed:
                    r.photos = new_list
                    stats["rooms_rows_touched"] += 1

            # Clients
            print("== CLIENTS ==")
            clients = (
                await db.execute(select(Client).where(Client.photo_url.isnot(None)))
            ).scalars().all()
            for c in clients:
                new_v = await migrate_value(http, c.photo_url, storage_root, stats, dry_run)
                if new_v is not None:
                    c.photo_url = new_v
                    stats["clients_rows_touched"] += 1

            if dry_run:
                print("\n[DRY-RUN] No DB writes.")
            else:
                await db.commit()
                print("\nDB committed.")

    print("\nStats:", stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
