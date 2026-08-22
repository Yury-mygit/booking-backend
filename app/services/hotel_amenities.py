"""TBB-65: валидация hotels.amenities по каталогу HotelAmenityOption.

Заменяет прежний enum-based whitelist (HotelAmenity): slug'и должны
существовать в каталоге и быть active=True на момент сохранения.
Каталог управляется админом (`/admin/settings/amenities/*`).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIError
from app.models.models import HotelAmenityOption


async def validate_hotel_amenity_slugs(
    db: AsyncSession,
    slugs: list[str] | None,
    previous: list[str] | None = None,
) -> None:
    """Проверить: **новые** slug'и есть в каталоге И active=True.

    `previous` — то что уже было сохранено у отеля. Grandfathered:
    slug'и из previous, которые админ deactivate'нул позже, разрешены
    (партнёр может сохранить другое поле формы, не трогая amenities).
    Партнёр не может *добавить* inactive slug — UI их не показывает.

    Пустой/None `slugs` — разрешено (удобств нет).
    """
    if not slugs:
        return
    prev = set(previous or [])
    added = [s for s in set(slugs) if s not in prev]
    if not added:
        return
    rows = (
        await db.execute(
            select(HotelAmenityOption.slug, HotelAmenityOption.active)
            .where(HotelAmenityOption.slug.in_(added))
        )
    ).all()
    known = {r.slug: r.active for r in rows}
    unknown = sorted(s for s in added if s not in known)
    inactive = sorted(s for s in added if s in known and not known[s])
    if unknown:
        raise APIError(
            400, "unknown_amenities",
            "Неизвестные удобства",
            detail={"unknown": unknown},
        )
    if inactive:
        raise APIError(
            400, "inactive_amenities",
            "Некоторые удобства отключены администратором",
            detail={"inactive": inactive},
        )
