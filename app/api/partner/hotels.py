"""Partner hotels: CRUD + dashboard + checklist + publish-stats.

Checklist решает can_publish (required-checks: photos, rooms, room-prices).
Stats — bookings_total/active, check-ins next 7d, revenue 30d.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.core.audit import audit
from app.services import scope
from app.models.models import (
    Booking,
    BookingStatus,
    Hotel,
    HotelStatus,
    Room,
)
from app.schemas.hotels import serialize_hotel_amenities
from app.schemas.partner import (
    ChecklistAction,
    ChecklistItem,
    HotelCreate,
    HotelDashboard,
    HotelPartnerView,
    HotelStats,
    HotelUpdate,
)
from app.utils import (
    gen_unique_hotel_slug,
)

router = APIRouter()  # prefix задан в partner/__init__.py


# ─── Hotels ────────────────────────────────────────────────────────────────

@router.get("/hotels", response_model=list[HotelPartnerView])
async def list_my_hotels(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    rows = (
        (
            await db.execute(
                select(Hotel)
                .where(Hotel.owner_user_id.in_(accessible_ids))
                .order_by(Hotel.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [HotelPartnerView.from_model(h) for h in rows]


@router.post("/hotels", response_model=HotelPartnerView, status_code=201)
async def create_hotel(
    payload: HotelCreate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    # Only the owner themselves can create hotels — staff cannot create hotels
    # for the owner. Requires a verified self-entry in accessible_owners.
    self_access = ctx.accessible_owners.get(ctx.user.id)
    if self_access is None or not self_access.is_self:
        raise APIError(403, "permission_denied", "Only the owner can create hotels")
    h = Hotel(
        owner_user_id=ctx.user.id,
        slug="__pending__",  # placeholder; replaced after flush() gives id
        name_ru=payload.name_ru,
        name_ky=payload.name_ky,
        name_en=payload.name_en,
        description_ru=payload.description_ru,
        description_ky=payload.description_ky,
        description_en=payload.description_en,
        city=payload.city,
        address=payload.address,
        lat=payload.lat,
        lng=payload.lng,
        photos=payload.photos,
        meals=payload.meals,
        amenities=serialize_hotel_amenities(payload.amenities),
        checkin_time=payload.checkin_time,
        checkout_time=payload.checkout_time,
    )
    db.add(h)
    await db.flush()  # need h.id for slug fallback
    h.slug = await gen_unique_hotel_slug(db, payload.name_en, h.id, exclude_id=h.id)
    await db.commit()
    await db.refresh(h)
    await audit(
        db, ctx,
        owner_user_id=h.owner_user_id,
        action="hotel.create",
        subject_type="hotel",
        subject_id=h.id,
        payload={"name_ru": h.name_ru, "city": h.city},
    )
    return HotelPartnerView.from_model(h)


@router.get("/hotels/{hotel_id}", response_model=HotelPartnerView)
async def get_my_hotel(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    return HotelPartnerView.from_model(await scope.get_my_hotel(db, ctx, hotel_id))


@router.get("/hotels/{hotel_id}/dashboard", response_model=HotelDashboard)
async def get_hotel_dashboard(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id)
    rooms = (
        (
            await db.execute(
                select(Room).where(Room.hotel_id == hotel_id).order_by(Room.id)
            )
        )
        .scalars()
        .all()
    )

    checks = _build_checklist(h, rooms)
    required_fails = sum(1 for c in checks if c.kind == "required" and not c.ok)
    stats = await _compute_hotel_stats(db, hotel_id)
    return HotelDashboard(
        can_publish=required_fails == 0,
        checks=checks,
        stats=stats,
    )


def _build_checklist(h: Hotel, rooms: list[Room]) -> list[ChecklistItem]:
    photo_count = len(h.photos or [])
    no_price_rooms = [r for r in rooms if not r.price_kgs or r.price_kgs <= 0]
    no_photo_rooms = [r for r in rooms if not (r.photos or [])]
    has_desc = any(
        (d or "").strip().__len__() >= 20
        for d in (h.description_ru, h.description_ky, h.description_en)
    )

    out: list[ChecklistItem] = []
    out.append(ChecklistItem(
        kind="required",
        ok=photo_count > 0,
        key="status.check.hotel_photos",
        action=None if photo_count > 0 else ChecklistAction(tab="photos"),
    ))
    out.append(ChecklistItem(
        kind="required",
        ok=len(rooms) > 0,
        key="status.check.has_rooms",
        action=None if rooms else ChecklistAction(nav="rooms"),
    ))
    if no_price_rooms:
        out.append(ChecklistItem(
            kind="required",
            ok=False,
            key="status.check.rooms_price_missing",
            params={"n": len(no_price_rooms)},
            action=ChecklistAction(room_id=no_price_rooms[0].id),
        ))
    else:
        out.append(ChecklistItem(
            kind="required",
            ok=len(rooms) > 0,
            key="status.check.rooms_price_ok",
        ))

    out.append(ChecklistItem(
        kind="recommended",
        ok=has_desc,
        key="status.check.description",
        action=None if has_desc else ChecklistAction(tab="description"),
    ))
    out.append(ChecklistItem(
        kind="recommended",
        ok=bool(h.address and h.address.strip()),
        key="status.check.address",
        action=None if (h.address and h.address.strip()) else ChecklistAction(tab="description"),
    ))
    out.append(ChecklistItem(
        kind="recommended",
        ok=h.lat is not None and h.lng is not None,
        key="status.check.coords",
        action=None if (h.lat is not None and h.lng is not None) else ChecklistAction(tab="description"),
    ))
    out.append(ChecklistItem(
        kind="recommended",
        ok=bool(h.name_ky and h.name_en),
        key="status.check.name_translations",
        action=None if (h.name_ky and h.name_en) else ChecklistAction(tab="description"),
    ))
    if rooms:
        if no_photo_rooms:
            out.append(ChecklistItem(
                kind="recommended",
                ok=False,
                key="status.check.rooms_no_photos",
                params={"n": len(no_photo_rooms)},
                action=ChecklistAction(room_id=no_photo_rooms[0].id),
            ))
        else:
            out.append(ChecklistItem(
                kind="recommended",
                ok=True,
                key="status.check.rooms_photos_ok",
            ))

    return out


async def _compute_hotel_stats(db: AsyncSession, hotel_id: int) -> HotelStats:
    today = date.today()
    in_7d = today + timedelta(days=7)
    since_30d = datetime.now(timezone.utc) - timedelta(days=30)

    room_ids_subq = select(Room.id).where(Room.hotel_id == hotel_id).scalar_subquery()

    is_active = case(
        (
            and_(
                Booking.status.in_([BookingStatus.pending, BookingStatus.paid]),
                Booking.check_out >= today,
            ),
            1,
        ),
        else_=0,
    )
    is_checkin_7d = case(
        (
            and_(
                Booking.status.not_in([BookingStatus.cancelled, BookingStatus.refunded]),
                Booking.check_in >= today,
                Booking.check_in <= in_7d,
            ),
            1,
        ),
        else_=0,
    )
    revenue_expr = case(
        (
            and_(
                Booking.status == BookingStatus.paid,
                Booking.created_at >= since_30d,
            ),
            Booking.total_kgs,
        ),
        else_=0,
    )

    row = (
        await db.execute(
            select(
                func.count(Booking.id).label("total"),
                func.coalesce(func.sum(is_active), 0).label("active"),
                func.coalesce(func.sum(is_checkin_7d), 0).label("checkins7d"),
                func.coalesce(func.sum(revenue_expr), 0).label("revenue30d"),
                func.max(Booking.created_at).label("last_at"),
            ).where(Booking.room_id.in_(room_ids_subq))
        )
    ).one()

    return HotelStats(
        bookings_total=row.total or 0,
        bookings_active=int(row.active or 0),
        checkins_next_7d=int(row.checkins7d or 0),
        revenue_kgs_30d=int(row.revenue30d or 0),
        last_booking_at=row.last_at,
    )


@router.put("/hotels/{hotel_id}", response_model=HotelPartnerView)
async def update_hotel(
    hotel_id: int,
    payload: HotelUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id, require_perm="manage_hotel")
    data = payload.model_dump(exclude_unset=True)
    if "amenities" in data and data["amenities"] is not None:
        data["amenities"] = serialize_hotel_amenities(payload.amenities)
    name_en_changed = "name_en" in data and data["name_en"] != h.name_en
    new_status = data.get("status")
    becomes_published = (
        "status" in data
        and new_status == HotelStatus.published
        and h.status != HotelStatus.published
    )
    for field, value in data.items():
        setattr(h, field, value)
    if name_en_changed:
        h.slug = await gen_unique_hotel_slug(db, h.name_en, h.id, exclude_id=h.id)
    if becomes_published:
        h.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(h)
    if becomes_published:
        action = "hotel.publish"
    elif "status" in data and data["status"] != HotelStatus.published:
        action = "hotel.unpublish"
    else:
        action = "hotel.update"
    await audit(
        db, ctx,
        owner_user_id=h.owner_user_id,
        action=action,
        subject_type="hotel",
        subject_id=h.id,
        payload={"changed_fields": list(data.keys())},
    )
    return HotelPartnerView.from_model(h)


@router.delete("/hotels/{hotel_id}", status_code=204)
async def delete_hotel(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id, require_perm="manage_hotel")
    today = date.today()
    has_active = (
        await db.execute(
            select(exists().where(
                and_(
                    Room.hotel_id == h.id,
                    Booking.room_id == Room.id,
                    Booking.status.in_([BookingStatus.pending, BookingStatus.paid]),
                    Booking.check_out >= today,
                )
            ))
        )
    ).scalar()
    if has_active:
        raise APIError(409, "conflict", "Hotel has active bookings; cannot hard-delete")
    await db.execute(
        delete(Booking).where(
            Booking.room_id.in_(select(Room.id).where(Room.hotel_id == h.id))
        )
    )
    snapshot = {"name_ru": h.name_ru, "city": h.city}
    owner_id_snap = h.owner_user_id
    hotel_id_snap = h.id
    await db.delete(h)
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="hotel.delete",
        subject_type="hotel",
        subject_id=hotel_id_snap,
        payload=snapshot,
    )
    return None


