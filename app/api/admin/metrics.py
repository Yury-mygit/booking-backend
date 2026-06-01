"""Admin /admin/metrics — глобальные counters."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext
from app.models.models import (
    Booking,
    Hotel,
    PartnerProfile,
    Payment,
    PaymentStatus,
    Room,
    User,
)
from app.schemas.admin import MetricsView

from ._deps import admin_only

router = APIRouter()


@router.get("/metrics", response_model=MetricsView)
async def metrics(
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    users_total = (await db.execute(select(func.count(User.id)))).scalar_one()
    users_by_role_rows = (
        await db.execute(select(User.role, func.count(User.id)).group_by(User.role))
    ).all()
    users_by_role = {r.value: c for r, c in users_by_role_rows}

    verified_partners = (
        await db.execute(
            select(func.count(PartnerProfile.user_id)).where(
                PartnerProfile.verified_at.is_not(None)
            )
        )
    ).scalar_one()

    hotels_total = (await db.execute(select(func.count(Hotel.id)))).scalar_one()
    hotels_by_status_rows = (
        await db.execute(select(Hotel.status, func.count(Hotel.id)).group_by(Hotel.status))
    ).all()
    hotels_by_status = {s.value: c for s, c in hotels_by_status_rows}

    rooms_total = (await db.execute(select(func.count(Room.id)))).scalar_one()

    bookings_total = (await db.execute(select(func.count(Booking.id)))).scalar_one()
    bookings_by_status_rows = (
        await db.execute(
            select(Booking.status, func.count(Booking.id)).group_by(Booking.status)
        )
    ).all()
    bookings_by_status = {s.value: c for s, c in bookings_by_status_rows}

    revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount_kgs), 0)).where(
                Payment.status == PaymentStatus.paid
            )
        )
    ).scalar_one()

    return MetricsView(
        users_total=users_total,
        users_by_role=users_by_role,
        verified_partners=verified_partners,
        hotels_total=hotels_total,
        hotels_by_status=hotels_by_status,
        rooms_total=rooms_total,
        bookings_total=bookings_total,
        bookings_by_status=bookings_by_status,
        revenue_kgs_paid=int(revenue or 0),
    )
