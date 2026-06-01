"""Admin /admin/hotels — list + set-status."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.models import Hotel, HotelStatus, User
from app.schemas.admin import AdminHotelView, HotelStatusUpdate

from ._deps import admin_only

router = APIRouter()


@router.get("/hotels", response_model=list[AdminHotelView])
async def list_all_hotels(
    status_filter: HotelStatus | None = Query(default=None, alias="status"),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Hotel, User.first_name)
        .join(User, User.id == Hotel.owner_user_id)
        .order_by(Hotel.created_at.desc())
        .limit(500)
    )
    if status_filter is not None:
        stmt = stmt.where(Hotel.status == status_filter)
    rows = (await db.execute(stmt)).all()
    return [
        AdminHotelView(
            id=h.id,
            owner_user_id=h.owner_user_id,
            owner_first_name=owner_name,
            name_ru=h.name_ru,
            city=h.city,
            status=h.status,
            created_at=h.created_at,
            updated_at=h.updated_at,
        )
        for h, owner_name in rows
    ]


@router.put("/hotels/{hotel_id}/status", response_model=AdminHotelView)
async def set_hotel_status(
    hotel_id: int,
    payload: HotelStatusUpdate,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    hotel = (await db.execute(select(Hotel).where(Hotel.id == hotel_id))).scalar_one_or_none()
    if hotel is None:
        raise APIError(404, "not_found", "Hotel not found")
    hotel.status = payload.status
    owner_name = (
        await db.execute(select(User.first_name).where(User.id == hotel.owner_user_id))
    ).scalar_one()
    await db.commit()
    await db.refresh(hotel)
    return AdminHotelView(
        id=hotel.id,
        owner_user_id=hotel.owner_user_id,
        owner_first_name=owner_name,
        name_ru=hotel.name_ru,
        city=hotel.city,
        status=hotel.status,
        created_at=hotel.created_at,
        updated_at=hotel.updated_at,
    )
