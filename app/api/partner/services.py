"""Partner hotel services (дополнительные платные услуги отеля)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.audit import audit
from app.services import scope
from app.models.models import (
    Hotel,
    HotelService,
)
from app.schemas.partner import (
    ServiceCreate,
    ServicePartnerView,
    ServiceUpdate,
)

router = APIRouter()  # prefix задан в partner/__init__.py


# ─── Services ──────────────────────────────────────────────────────────────

@router.get("/hotels/{hotel_id}/services", response_model=list[ServicePartnerView])
async def list_services(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    await scope.get_my_hotel(db, ctx, hotel_id)
    rows = (
        (
            await db.execute(
                select(HotelService).where(HotelService.hotel_id == hotel_id).order_by(HotelService.id)
            )
        )
        .scalars()
        .all()
    )
    return [ServicePartnerView.from_model(s) for s in rows]


@router.post("/hotels/{hotel_id}/services", response_model=ServicePartnerView, status_code=201)
async def create_service(
    hotel_id: int,
    payload: ServiceCreate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id, require_perm="manage_hotel")
    s = HotelService(hotel_id=hotel_id, **payload.model_dump())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    await audit(
        db, ctx,
        owner_user_id=h.owner_user_id,
        action="service.create",
        subject_type="service",
        subject_id=s.id,
        payload={"hotel_id": hotel_id, "name_ru": s.name_ru},
    )
    return ServicePartnerView.from_model(s)


@router.put("/hotels/{hotel_id}/services/{service_id}", response_model=ServicePartnerView)
async def update_service(
    hotel_id: int,
    service_id: int,
    payload: ServiceUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    s = await scope.get_my_service(db, ctx, hotel_id, service_id, require_perm="manage_hotel")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(s, field, value)
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    await db.commit()
    await db.refresh(s)
    await audit(
        db, ctx,
        owner_user_id=hotel_owner_id,
        action="service.update",
        subject_type="service",
        subject_id=s.id,
        payload={"hotel_id": hotel_id, "changed_fields": list(data.keys())},
    )
    return ServicePartnerView.from_model(s)


@router.delete("/hotels/{hotel_id}/services/{service_id}", status_code=204)
async def delete_service(
    hotel_id: int,
    service_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    s = await scope.get_my_service(db, ctx, hotel_id, service_id, require_perm="manage_hotel")
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    snapshot = {"hotel_id": hotel_id, "name_ru": s.name_ru}
    sid_snap = s.id
    await db.delete(s)
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=hotel_owner_id,
        action="service.delete",
        subject_type="service",
        subject_id=sid_snap,
        payload=snapshot,
    )
    return None


