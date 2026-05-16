import re
import secrets
from datetime import date, timedelta
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

BOOKING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BOOKING_CODE_LEN = 8


def gen_booking_code() -> str:
    return "".join(secrets.choice(BOOKING_CODE_ALPHABET) for _ in range(BOOKING_CODE_LEN))


def date_range_nights(check_in: date, check_out: date) -> Iterator[date]:
    """Yield each night between check_in (inclusive) and check_out (exclusive)."""
    d = check_in
    while d < check_out:
        yield d
        d += timedelta(days=1)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str | None) -> str:
    """ASCII-only slug. Cyrillic / non-Latin → empty (caller must fallback)."""
    if not s:
        return ""
    s = s.lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s[:60]


async def gen_unique_hotel_slug(
    db: AsyncSession,
    name_en: str | None,
    hotel_id: int,
    exclude_id: int | None = None,
) -> str:
    """Pick a unique slug for a hotel. Fallback: hotel-{id}."""
    from app.models.models import Hotel  # lazy import to avoid circular

    base = slugify(name_en) or f"hotel-{hotel_id}"
    candidate = base
    n = 0
    while True:
        stmt = select(Hotel.id).where(Hotel.slug == candidate)
        if exclude_id is not None:
            stmt = stmt.where(Hotel.id != exclude_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"
