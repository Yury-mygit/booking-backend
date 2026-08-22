"""Admin CRUD для каталога удобств отеля (TBB-65).

`/admin/amenity-options` — 4 endpoints:
- GET  ?section=general — список (all, включая inactive)
- POST — create (принимает name+description+section; slug auto)
- PATCH /{id} — обновить name/description/active
- POST /reorder — принимает {section, order:[id,...]} → sort_order
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.models import HotelAmenityOption
from app.services import amenity_events
from app.utils import slugify_ru

from ._deps import admin_only

router = APIRouter()

SECTIONS = ("general", "dining")


class AmenityOptionView(BaseModel):
    id: int
    section: str
    slug: str
    name: str
    description: str
    active: bool
    sort_order: int
    created_at: datetime


class AmenityOptionCreate(BaseModel):
    section: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=200)


class AmenityOptionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None


class AmenityReorder(BaseModel):
    section: str = Field(min_length=1, max_length=16)
    order: list[int] = Field(min_length=1)


def _slug_from_name(name: str) -> str:
    """Underscore-стиль (совпадает с seed'ами: `free_tea_coffee`)."""
    base = slugify_ru(name).replace("-", "_")
    return base or "opt"


async def _gen_unique_slug(db: AsyncSession, name: str) -> str:
    base = _slug_from_name(name)
    cand = base
    n = 1
    while True:
        exists = (
            await db.execute(
                select(HotelAmenityOption.id).where(HotelAmenityOption.slug == cand)
            )
        ).scalar_one_or_none()
        if exists is None:
            return cand
        n += 1
        cand = f"{base}_{n}"


@router.get("/amenity-options", response_model=list[AmenityOptionView])
async def list_amenity_options(
    section: str | None = Query(default=None),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(HotelAmenityOption)
    if section is not None:
        if section not in SECTIONS:
            raise APIError(400, "unknown_section", "Неизвестная секция")
        stmt = stmt.where(HotelAmenityOption.section == section)
    stmt = stmt.order_by(HotelAmenityOption.section, HotelAmenityOption.sort_order)
    rows = (await db.execute(stmt)).scalars().all()
    return [AmenityOptionView.model_validate(r, from_attributes=True) for r in rows]


@router.post("/amenity-options", response_model=AmenityOptionView, status_code=201)
async def create_amenity_option(
    payload: AmenityOptionCreate,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    if payload.section not in SECTIONS:
        raise APIError(400, "unknown_section", "Неизвестная секция")
    slug = await _gen_unique_slug(db, payload.name)
    # sort_order = max+1 в секции (в хвост списка)
    max_order = (
        await db.execute(
            select(func.coalesce(func.max(HotelAmenityOption.sort_order), -1)).where(
                HotelAmenityOption.section == payload.section
            )
        )
    ).scalar_one()
    row = HotelAmenityOption(
        section=payload.section,
        slug=slug,
        name=payload.name,
        description=payload.description,
        active=False,  # новый вариант выключен по умолчанию
        sort_order=int(max_order) + 1,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await amenity_events.publish_refresh()
    return AmenityOptionView.model_validate(row, from_attributes=True)


@router.patch("/amenity-options/{option_id}", response_model=AmenityOptionView)
async def update_amenity_option(
    option_id: int,
    payload: AmenityOptionUpdate,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(HotelAmenityOption).where(HotelAmenityOption.id == option_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise APIError(404, "not_found", "Вариант не найден")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    await amenity_events.publish_refresh()
    return AmenityOptionView.model_validate(row, from_attributes=True)


@router.post("/amenity-options/reorder", status_code=204)
async def reorder_amenity_options(
    payload: AmenityReorder,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    if payload.section not in SECTIONS:
        raise APIError(400, "unknown_section", "Неизвестная секция")
    section_ids = set(
        (
            await db.execute(
                select(HotelAmenityOption.id).where(
                    HotelAmenityOption.section == payload.section
                )
            )
        )
        .scalars()
        .all()
    )
    if set(payload.order) != section_ids:
        raise APIError(
            400, "reorder_mismatch",
            "Порядок должен содержать ровно все id секции",
            detail={
                "expected": sorted(section_ids),
                "got": sorted(payload.order),
            },
        )
    # Один UPDATE через CASE.
    whens = {id_: idx for idx, id_ in enumerate(payload.order)}
    await db.execute(
        HotelAmenityOption.__table__.update()
        .where(HotelAmenityOption.section == payload.section)
        .values(sort_order=case(whens, value=HotelAmenityOption.id))
    )
    await db.commit()
    await amenity_events.publish_refresh()
