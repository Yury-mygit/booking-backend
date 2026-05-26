"""Admin endpoints (/admin/*, require_role(UserRole.admin)).

Users: list + verify-partner / promote-admin / revoke-partner / demote-admin.
Hotels: list + set-status. Bookings: list + cancel. Metrics: counters.

Verify/revoke-partner — переключают `partner_profile.verified_at` и
`user.role` (partner ↔ client). Promote/demote-admin — меняют `user.role`
напрямую, без profile-связи. Revoke и demote также чистят активные
сессии затронутой роли, чтобы фронт не показывал устаревшую плашку
доступа после server-side даунгрейда.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import get_db
from app.core.deps import AuthContext, require_role
from app.core.exceptions import APIError
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Client,
    Hotel,
    HotelStatus,
    PartnerProfile,
    Payment,
    PaymentStatus,
    Room,
    Session,
    User,
    UserRole,
)
from app.schemas.admin import (
    AdminBookingView,
    AdminHotelView,
    AdminUserView,
    HotelStatusUpdate,
    MetricsView,
)

router = APIRouter(prefix="/admin", tags=["admin"])
admin_only = require_role(UserRole.admin)


# ─── Users ─────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[AdminUserView])
async def list_users(
    role: UserRole | None = Query(default=None),
    verified: bool | None = Query(default=None),
    pending: bool | None = Query(default=None),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import Client
    # Aggregate hotels/bookings per user in subqueries so the main row stays flat.
    hotels_cnt = (
        select(Hotel.owner_user_id.label("uid"), func.count(Hotel.id).label("cnt"))
        .group_by(Hotel.owner_user_id)
        .subquery()
    )
    bookings_cnt = (
        select(Client.user_id.label("uid"), func.count(Booking.id).label("cnt"))
        .join(Booking, Booking.client_id == Client.id)
        .where(Client.user_id.is_not(None))
        .group_by(Client.user_id)
        .subquery()
    )

    stmt = (
        select(
            User,
            PartnerProfile.user_id.label("pp_uid"),
            PartnerProfile.verified_at,
            hotels_cnt.c.cnt.label("hcnt"),
            bookings_cnt.c.cnt.label("bcnt"),
        )
        .outerjoin(PartnerProfile, PartnerProfile.user_id == User.id)
        .outerjoin(hotels_cnt, hotels_cnt.c.uid == User.id)
        .outerjoin(bookings_cnt, bookings_cnt.c.uid == User.id)
    )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if verified is True:
        stmt = stmt.where(PartnerProfile.verified_at.is_not(None))
    elif verified is False:
        stmt = stmt.where(PartnerProfile.verified_at.is_(None))
    if pending is True:
        stmt = stmt.where(
            PartnerProfile.user_id.is_not(None), PartnerProfile.verified_at.is_(None)
        )
    stmt = stmt.order_by(User.created_at.desc()).limit(500)

    rows = (await db.execute(stmt)).all()
    return [
        AdminUserView.from_model(
            u,
            verified_at=verified_at,
            has_profile=pp_uid is not None,
            hotels_count=hcnt or 0,
            bookings_count=bcnt or 0,
        )
        for u, pp_uid, verified_at, hcnt, bcnt in rows
    ]


@router.post("/users/{user_id}/verify-partner", response_model=AdminUserView)
async def verify_partner(
    user_id: int,
    company_name: str = Query(...),
    legal_inn: str | None = Query(default=None),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    if user.role != UserRole.partner:
        raise APIError(400, "bad_request", "User is not a partner")

    profile = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user_id))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if profile is None:
        db.add(
            PartnerProfile(
                user_id=user_id,
                company_name=company_name,
                legal_inn=legal_inn,
                verified_at=now,
            )
        )
    else:
        profile.company_name = company_name
        profile.legal_inn = legal_inn
        profile.verified_at = now
    await db.commit()

    return AdminUserView.from_model(user, verified_at=now, has_profile=True,
                                hotels_count=0, bookings_count=0)


@router.post("/users/{user_id}/promote-admin", response_model=AdminUserView)
async def promote_admin(
    user_id: int,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    user.role = UserRole.admin
    await db.commit()
    return AdminUserView.from_model(user, verified_at=None, has_profile=False,
                                hotels_count=0, bookings_count=0)


@router.post("/users/{user_id}/revoke-partner", response_model=AdminUserView)
async def revoke_partner(
    user_id: int,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Drop partner_profile, reset role to client, kill partner sessions.
    Hotels owned by this user stay (FK survives) but require_verified_partner
    will start returning 403 — the user can reapply via the partner bot,
    which recreates the profile in pending state.

    Без сброса `users.role` и удаления partner-сессий фронт продолжает
    показывать партнёрскую плашку (по `role` из старой /auth/tg) при том
    что бэк уже режет доступ. По аналогии с demote_admin."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    profile = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        raise APIError(409, "conflict", "User has no partner profile")
    await db.execute(delete(PartnerProfile).where(PartnerProfile.user_id == user_id))
    user.role = UserRole.client
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.commit()
    await db.refresh(user)
    return AdminUserView.from_model(user, verified_at=None, has_profile=False,
                                hotels_count=0, bookings_count=0)


@router.post("/users/{user_id}/demote-admin", response_model=AdminUserView)
async def demote_admin(
    user_id: int,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Reverse promote-admin. Superadmins are immune (403). New role:
    partner if a partner_profile exists, otherwise client. Existing admin
    sessions for this user are deleted so the demotion takes effect now."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    if user.role != UserRole.admin:
        raise APIError(400, "bad_request", "User is not an admin")
    if user.is_superadmin:
        raise APIError(403, "forbidden", "Superadmin cannot be demoted")

    profile = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user_id))
    ).scalar_one_or_none()
    user.role = UserRole.partner if profile is not None else UserRole.client
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.commit()
    await db.refresh(user)
    return AdminUserView.from_model(
        user,
        verified_at=profile.verified_at if profile is not None else None,
        has_profile=profile is not None,
        hotels_count=0,
        bookings_count=0,
    )


# ─── Hotels ────────────────────────────────────────────────────────────────

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


# ─── Bookings ──────────────────────────────────────────────────────────────

@router.get("/bookings", response_model=list[AdminBookingView])
async def list_all_bookings(
    status_filter: BookingStatus | None = Query(default=None, alias="status"),
    hotel_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Booking, Room, Hotel, Client)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .join(Client, Client.id == Booking.client_id)
        .order_by(Booking.created_at.desc())
        .limit(500)
    )
    if status_filter is not None:
        stmt = stmt.where(Booking.status == status_filter)
    if hotel_id is not None:
        stmt = stmt.where(Hotel.id == hotel_id)
    rows = (await db.execute(stmt)).all()
    return [
        AdminBookingView(
            id=b.id,
            code=b.code,
            client_id=b.client_id,
            client_first_name=c.first_name,
            room_id=b.room_id,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            check_in=b.check_in,
            check_out=b.check_out,
            guests=b.guests,
            total_kgs=b.total_kgs,
            status=b.status,
            created_at=b.created_at,
        )
        for b, r, h, c in rows
    ]


@router.post("/bookings/{code}/cancel", response_model=AdminBookingView)
async def cancel_booking(
    code: str,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    booking = (
        await db.execute(select(Booking).where(Booking.code == code).with_for_update())
    ).scalar_one_or_none()
    if booking is None:
        raise APIError(404, "not_found", "Booking not found")
    if booking.status in (BookingStatus.cancelled, BookingStatus.refunded):
        raise APIError(409, "conflict", f"Booking already {booking.status.value}")

    # Free availability rows that we booked: rows with status=booked in range.
    avail_rows = (
        (
            await db.execute(
                select(Availability)
                .where(
                    Availability.room_id == booking.room_id,
                    Availability.date >= booking.check_in,
                    Availability.date < booking.check_out,
                    Availability.status == AvailabilityStatus.booked,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for a in avail_rows:
        if a.price_override is None:
            await db.execute(
                delete(Availability).where(
                    Availability.room_id == a.room_id, Availability.date == a.date
                )
            )
        else:
            a.status = AvailabilityStatus.free

    # If there are paid payments, this would warrant a refund — we just flip the
    # booking status; payments table is not auto-touched (admin handles refund
    # via payment provider separately).
    booking.status = BookingStatus.cancelled
    hotel_id_for_pub = (
        await db.execute(select(Room.hotel_id).where(Room.id == booking.room_id))
    ).scalar_one()
    await db.commit()
    await pubsub.publish_refresh(hotel_id_for_pub)

    row = (
        await db.execute(
            select(Room, Hotel, Client)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .join(Client, Client.id == booking.client_id)
            .where(Room.id == booking.room_id)
        )
    ).first()
    _, hotel, client = row
    return AdminBookingView(
        id=booking.id,
        code=booking.code,
        client_id=booking.client_id,
        client_first_name=client.first_name,
        room_id=booking.room_id,
        hotel_id=hotel.id,
        hotel_name_ru=hotel.name_ru,
        check_in=booking.check_in,
        check_out=booking.check_out,
        guests=booking.guests,
        total_kgs=booking.total_kgs,
        status=booking.status,
        created_at=booking.created_at,
    )


# ─── Metrics ───────────────────────────────────────────────────────────────

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
