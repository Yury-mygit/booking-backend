"""Общее для сидов: город → направление (TBB-73).

С TBB-68 `hotels.destination_id` NOT NULL, а города хранятся кириллицей.
Соответствие — то же, что в миграции `destinations_catalog` (CITY_NORMALIZE);
направления сеет миграция, сид их не создаёт.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Destination

# Город (кириллицей, как в hotels.city) → slug направления.
CITY_DESTINATION = {
    "Бишкек": "bishkek",
    "Чолпон-Ата": "issyk-kul",
    "Каракол": "issyk-kul",
    "Нарын": "naryn",
    "Ош": "osh",
}


async def destination_id_for(db: AsyncSession, city: str) -> int:
    slug = CITY_DESTINATION[city]
    dest_id = (
        await db.execute(select(Destination.id).where(Destination.slug == slug))
    ).scalar_one_or_none()
    if dest_id is None:
        raise RuntimeError(f"нет направления {slug!r} — выполнить `alembic upgrade head`")
    return dest_id
