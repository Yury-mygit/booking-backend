"""Partner endpoints (/p/*, require_partner_or_staff).

⚠ FILE-SIZE WARNING: 2000+ строк, 10 доменов. План разбиения — см.
   `~/claude-workspace/history/2026-05-26-booking-backend-refactor-plan.md`
   (Этап 4). До разбиения секции внутри — в порядке:
     hotels (CRUD + dashboard + checklist + stats) → rooms → availability
     → services → bookings + walk-in → rooms-flat → clients → staff
     → staff invites → audit.

Авторизация: `require_partner_or_staff` (alias `require_verified_partner`)
проверяет `accessible_owners` (verified partner_profile ИЛИ staff
membership). Staff-permissions (manage_hotel/rooms/bookings/staff)
проверяются точечно через `scope_owner_ids` + flags на PartnerStaff.

Все write-операции пишут в audit_log через `audit(...)` helper —
читается на /p/audit и /p/audit.csv.
"""
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, current_user, require_verified_partner
from app.core.exceptions import APIError
from app.core.audit import audit
from app.services import scope
from app.models.models import (
    AuditLog,
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Client,
    Hotel,
    HotelService,
    HotelStatus,
    PartnerStaff,
    PartnerStaffInvite,
    Room,
    User,
    UserRole,
)
from app.schemas.partner import (
    AuditEntryView,
    AvailabilityBatchUpdate,
    AvailabilityRowOut,
    ClientLookup,
    ClientPartnerView,
    ClientUpdate,
    ChecklistAction,
    ChecklistItem,
    HotelCreate,
    HotelDashboard,
    HotelPartnerView,
    HotelStats,
    HotelUpdate,
    OwnerAccess,
    PartnerBookingPostpaySet,
    PartnerBookingView,
    RoomCreate,
    RoomFlatView,
    RoomPartnerView,
    RoomUpdate,
    ServiceCreate,
    ServicePartnerView,
    ServiceUpdate,
    StaffCreate,
    StaffInviteAccept,
    StaffInviteCreate,
    StaffInviteView,
    StaffPerms,
    StaffUpdate,
    StaffView,
    WalkinBookingCreate,
)
from app.utils import (
    date_range_nights,
    gen_booking_code,
    gen_unique_hotel_slug,
    normalize_email,
    normalize_phone,
)

router = APIRouter(prefix="/p", tags=["partner"])


def _to_hotel_view(h: Hotel) -> HotelPartnerView:
    return HotelPartnerView(
        id=h.id,
        slug=h.slug,
        name_ru=h.name_ru,
        name_ky=h.name_ky,
        name_en=h.name_en,
        description_ru=h.description_ru,
        description_ky=h.description_ky,
        description_en=h.description_en,
        city=h.city,
        address=h.address,
        lat=float(h.lat) if h.lat is not None else None,
        lng=float(h.lng) if h.lng is not None else None,
        photos=h.photos or [],
        status=h.status,
        published_at=h.published_at,
        created_at=h.created_at,
        updated_at=h.updated_at,
    )


def _to_room_view(r: Room) -> RoomPartnerView:
    return RoomPartnerView(
        id=r.id,
        hotel_id=r.hotel_id,
        name_ru=r.name_ru,
        name_ky=r.name_ky,
        name_en=r.name_en,
        description_ru=r.description_ru,
        description_ky=r.description_ky,
        description_en=r.description_en,
        capacity=r.capacity,
        price_kgs=r.price_kgs,
        floor=r.floor,
        beds=r.beds,
        photos=r.photos or [],
        created_at=r.created_at,
    )


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
    return [_to_hotel_view(h) for h in rows]


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
    return _to_hotel_view(h)


@router.get("/hotels/{hotel_id}", response_model=HotelPartnerView)
async def get_my_hotel(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    return _to_hotel_view(await scope.get_my_hotel(db, ctx, hotel_id))


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
    return _to_hotel_view(h)


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


# ─── Rooms (nested) ────────────────────────────────────────────────────────

@router.get("/hotels/{hotel_id}/rooms", response_model=list[RoomPartnerView])
async def list_rooms(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    await scope.get_my_hotel(db, ctx, hotel_id)
    rows = (
        (
            await db.execute(
                select(Room).where(Room.hotel_id == hotel_id).order_by(Room.id)
            )
        )
        .scalars()
        .all()
    )
    return [_to_room_view(r) for r in rows]


@router.post("/hotels/{hotel_id}/rooms", response_model=RoomPartnerView, status_code=201)
async def create_room(
    hotel_id: int,
    payload: RoomCreate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id, require_perm="manage_rooms")
    r = Room(hotel_id=hotel_id, **payload.model_dump())
    db.add(r)
    await db.commit()
    await db.refresh(r)
    await audit(
        db, ctx,
        owner_user_id=h.owner_user_id,
        action="room.create",
        subject_type="room",
        subject_id=r.id,
        payload={"hotel_id": hotel_id, "name_ru": r.name_ru, "capacity": r.capacity, "price_kgs": r.price_kgs},
    )
    return _to_room_view(r)


@router.get("/hotels/{hotel_id}/rooms/{room_id}", response_model=RoomPartnerView)
async def get_room(
    hotel_id: int,
    room_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    return _to_room_view(await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id))


async def _room_has_active_bookings(db: AsyncSession, room_id: int) -> bool:
    today = date.today()
    return bool(
        (
            await db.execute(
                select(exists().where(
                    and_(
                        Booking.room_id == room_id,
                        Booking.status.in_([BookingStatus.pending, BookingStatus.paid]),
                        Booking.check_out >= today,
                    )
                ))
            )
        ).scalar()
    )


@router.put("/hotels/{hotel_id}/rooms/{room_id}", response_model=RoomPartnerView)
async def update_room(
    hotel_id: int,
    room_id: int,
    payload: RoomUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id, require_perm="manage_rooms")
    data = payload.model_dump(exclude_unset=True)
    if "capacity" in data and data["capacity"] != r.capacity:
        if await _room_has_active_bookings(db, r.id):
            raise APIError(
                409, "conflict", "Cannot change capacity while active bookings exist"
            )
    for field, value in data.items():
        setattr(r, field, value)
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    await db.commit()
    await db.refresh(r)
    await audit(
        db, ctx,
        owner_user_id=hotel_owner_id,
        action="room.update",
        subject_type="room",
        subject_id=r.id,
        payload={"hotel_id": hotel_id, "changed_fields": list(data.keys())},
    )
    return _to_room_view(r)


@router.delete("/hotels/{hotel_id}/rooms/{room_id}", status_code=204)
async def delete_room(
    hotel_id: int,
    room_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id, require_perm="manage_rooms")
    if await _room_has_active_bookings(db, r.id):
        raise APIError(409, "conflict", "Room has active bookings")
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    snapshot = {"hotel_id": hotel_id, "name_ru": r.name_ru, "capacity": r.capacity}
    room_id_snap = r.id
    await db.delete(r)
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=hotel_owner_id,
        action="room.delete",
        subject_type="room",
        subject_id=room_id_snap,
        payload=snapshot,
    )
    return None


# ─── Availability ──────────────────────────────────────────────────────────

@router.get(
    "/hotels/{hotel_id}/rooms/{room_id}/availability",
    response_model=list[AvailabilityRowOut],
)
async def get_availability(
    hotel_id: int,
    room_id: int,
    from_: date = Query(alias="from"),
    to: date = Query(...),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id)
    if to <= from_:
        raise APIError(400, "bad_request", "to must be after from")
    rows = (
        (
            await db.execute(
                select(Availability)
                .where(
                    Availability.room_id == room_id,
                    Availability.date >= from_,
                    Availability.date < to,
                )
                .order_by(Availability.date)
            )
        )
        .scalars()
        .all()
    )
    return [
        AvailabilityRowOut(date=a.date, status=a.status, price_override=a.price_override)
        for a in rows
    ]


@router.put(
    "/hotels/{hotel_id}/rooms/{room_id}/availability",
    response_model=list[AvailabilityRowOut],
)
async def update_availability(
    hotel_id: int,
    room_id: int,
    payload: AvailabilityBatchUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id, require_perm="manage_rooms")

    # Reject any attempt to set 'booked' through partner endpoint.
    for n in payload.nights:
        if n.status == AvailabilityStatus.booked:
            raise APIError(
                400,
                "bad_request",
                f"Cannot set status=booked manually (date {n.date.isoformat()})",
            )

    # Refuse to overwrite existing 'booked' rows.
    incoming_dates = [n.date for n in payload.nights]
    if incoming_dates:
        existing_booked = (
            (
                await db.execute(
                    select(Availability.date).where(
                        Availability.room_id == room_id,
                        Availability.date.in_(incoming_dates),
                        Availability.status == AvailabilityStatus.booked,
                    )
                )
            )
            .scalars()
            .all()
        )
        if existing_booked:
            raise APIError(
                409,
                "conflict",
                f"Cannot modify booked nights: {[d.isoformat() for d in existing_booked]}",
            )

    # Apply: status=free && price_override=None → delete row; else upsert.
    for n in payload.nights:
        if n.status == AvailabilityStatus.free and n.price_override is None:
            await db.execute(
                delete(Availability).where(
                    Availability.room_id == room_id, Availability.date == n.date
                )
            )
            continue
        stmt = (
            pg_insert(Availability)
            .values(
                room_id=room_id,
                date=n.date,
                status=n.status,
                price_override=n.price_override,
            )
            .on_conflict_do_update(
                index_elements=["room_id", "date"],
                set_={"status": n.status, "price_override": n.price_override},
            )
        )
        await db.execute(stmt)
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    await db.commit()
    if payload.nights:
        await audit(
            db, ctx,
            owner_user_id=hotel_owner_id,
            action="room.availability_update",
            subject_type="room",
            subject_id=room_id,
            payload={"nights_count": len(payload.nights), "hotel_id": hotel_id},
        )

    if incoming_dates:
        rows = (
            (
                await db.execute(
                    select(Availability)
                    .where(
                        Availability.room_id == room_id,
                        Availability.date.in_(incoming_dates),
                    )
                    .order_by(Availability.date)
                )
            )
            .scalars()
            .all()
        )
    else:
        rows = []
    return [
        AvailabilityRowOut(date=a.date, status=a.status, price_override=a.price_override)
        for a in rows
    ]


# ─── Services ──────────────────────────────────────────────────────────────

def _to_service_view(s: HotelService) -> ServicePartnerView:
    return ServicePartnerView(
        id=s.id,
        hotel_id=s.hotel_id,
        name_ru=s.name_ru,
        name_ky=s.name_ky,
        name_en=s.name_en,
        price_kgs=s.price_kgs,
        created_at=s.created_at,
    )


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
    return [_to_service_view(s) for s in rows]


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
    return _to_service_view(s)


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
    return _to_service_view(s)


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


# ─── Incoming bookings ─────────────────────────────────────────────────────

@router.get("/bookings", response_model=list[PartnerBookingView])
async def list_incoming_bookings(
    status_filter: BookingStatus | None = Query(default=None, alias="status"),
    hotel_id: int | None = Query(default=None),
    owner_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    stmt = (
        select(Booking, Room, Hotel, Client)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .join(Client, Client.id == Booking.client_id)
        .where(Hotel.owner_user_id.in_(accessible_ids))
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        stmt = stmt.where(Booking.status == status_filter)
    if hotel_id is not None:
        stmt = stmt.where(Hotel.id == hotel_id)

    rows = (await db.execute(stmt)).all()
    return [_to_partner_booking(b, r, h, c) for b, r, h, c in rows]


def _to_partner_booking(b: Booking, r: Room, h: Hotel, c: Client) -> PartnerBookingView:
    return PartnerBookingView(
        id=b.id,
        code=b.code,
        room_id=r.id,
        room_name_ru=r.name_ru,
        hotel_id=h.id,
        hotel_name_ru=h.name_ru,
        client_first_name=c.first_name,
        check_in=b.check_in,
        check_out=b.check_out,
        guests=b.guests,
        total_kgs=b.total_kgs,
        status=b.status,
        postpay=b.postpay,
        confirmed=b.confirmed,
        created_at=b.created_at,
    )


@router.post("/bookings/{code}/confirm", response_model=PartnerBookingView)
async def confirm_booking(
    code: str,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """Partner guarantee to accept the guest. Sets confirmed=true; does not
    touch payment status. Online-paid bookings auto-confirm via /c/payments."""
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status != BookingStatus.pending:
        raise APIError(409, "conflict", f"Booking is {b.status.value}, cannot confirm")
    if b.confirmed:
        raise APIError(409, "conflict", "Booking is already confirmed")
    b.confirmed = True
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.confirm",
        subject_type="booking",
        subject_id=b.id,
        payload={"code": b.code, "hotel_id": hotel_id_for_pub},
    )
    return _to_partner_booking(b, r, h, c)


@router.post("/bookings/{code}/mark-paid", response_model=PartnerBookingView)
async def mark_paid(
    code: str,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """Postpay flow: record physical cash receipt → status=paid + confirmed=true.
    Only for postpay bookings; online ones become paid automatically through
    /c/payments and don't need this."""
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status != BookingStatus.pending:
        raise APIError(409, "conflict", f"Booking is {b.status.value}, cannot mark paid")
    if not b.postpay:
        raise APIError(
            400,
            "bad_request",
            "Only postpay bookings accept manual mark-paid; online bookings settle via /c/payments",
        )
    b.status = BookingStatus.paid
    b.confirmed = True  # paid implies confirmed
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.mark_paid",
        subject_type="booking",
        subject_id=b.id,
        payload={"code": b.code, "hotel_id": hotel_id_for_pub},
    )
    return _to_partner_booking(b, r, h, c)


@router.post("/bookings/{code}/postpay", response_model=PartnerBookingView)
async def set_postpay(
    code: str,
    payload: PartnerBookingPostpaySet,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """Toggle postpay flag on a booking. Postpay = «оплата у стойки», 24h
    auto-cancel skips it and partner confirms manually after physical payment."""
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status not in (BookingStatus.pending, BookingStatus.paid):
        raise APIError(409, "conflict", f"Cannot change postpay on {b.status.value} booking")
    b.postpay = payload.postpay
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.postpay",
        subject_type="booking",
        subject_id=b.id,
        payload={"code": b.code, "postpay": b.postpay},
    )
    return _to_partner_booking(b, r, h, c)


@router.post("/bookings/{code}/cancel", response_model=PartnerBookingView)
async def cancel_booking(
    code: str,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    b, r, h, c = await scope.get_my_booking(db, ctx, code, require_perm="manage_bookings")
    if b.status in (BookingStatus.cancelled, BookingStatus.refunded):
        raise APIError(409, "conflict", f"Booking is already {b.status.value}")

    avail_rows = (
        (
            await db.execute(
                select(Availability)
                .where(
                    Availability.room_id == b.room_id,
                    Availability.date >= b.check_in,
                    Availability.date < b.check_out,
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

    b.status = BookingStatus.cancelled
    hotel_id_for_pub = h.id
    owner_id_snap = h.owner_user_id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="booking.cancel",
        subject_type="booking",
        subject_id=b.id,
        payload={"code": b.code, "hotel_id": hotel_id_for_pub},
    )
    return _to_partner_booking(b, r, h, c)


# ─── Walk-in bookings ─────────────────────────────────────────────────────

async def _find_or_create_client_for_walkin(
    db: AsyncSession, payload: WalkinBookingCreate
) -> Client:
    """Dedup walk-in clients by normalized phone / email; create otherwise."""
    norm_phone = normalize_phone(payload.phone)
    norm_email = normalize_email(payload.email)
    existing: Client | None = None
    if norm_phone:
        existing = (
            await db.execute(select(Client).where(Client.phone == norm_phone))
        ).scalar_one_or_none()
    if existing is None and norm_email:
        existing = (
            await db.execute(select(Client).where(Client.email == norm_email))
        ).scalar_one_or_none()
    if existing is not None:
        return existing
    c = Client(
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone=norm_phone,
        email=norm_email,
        doc_kind=payload.doc_kind,
        doc_number=payload.doc_number,
    )
    db.add(c)
    await db.flush()
    return c


@router.post("/walkin-bookings", response_model=PartnerBookingView, status_code=201)
async def create_walkin_booking(
    payload: WalkinBookingCreate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    if payload.check_out <= payload.check_in:
        raise APIError(400, "bad_request", "check_out must be after check_in")
    today = date.today()
    if payload.check_in < today:
        raise APIError(400, "bad_request", "check_in is in the past")

    # Verify room belongs to an accessible owner's hotel + lock it.
    accessible_ids = list(ctx.accessible_owners.keys())
    row = (
        await db.execute(
            select(Room, Hotel)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Room.id == payload.room_id, Hotel.owner_user_id.in_(accessible_ids))
            .with_for_update(of=Room)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Room not found")
    room, hotel = row
    if not ctx.accessible_owners[hotel.owner_user_id].has("manage_bookings"):
        raise APIError(403, "permission_denied", "Missing permission: manage_bookings")
    if room.capacity < payload.guests:
        raise APIError(400, "bad_request", "Too many guests for this room")

    # Lock availability rows in range; verify nothing is blocked/booked.
    nights = list(date_range_nights(payload.check_in, payload.check_out))
    existing_av = (
        await db.execute(
            select(Availability)
            .where(
                Availability.room_id == room.id,
                Availability.date >= payload.check_in,
                Availability.date < payload.check_out,
            )
            .with_for_update()
        )
    ).scalars().all()
    by_date = {a.date: a for a in existing_av}
    for d in nights:
        a = by_date.get(d)
        if a is not None and a.status in (AvailabilityStatus.blocked, AvailabilityStatus.booked):
            raise APIError(409, "conflict", f"Night {d.isoformat()} is not available")

    total = 0
    for d in nights:
        a = by_date.get(d)
        total += a.price_override if (a and a.price_override is not None) else room.price_kgs

    for d in nights:
        stmt = (
            pg_insert(Availability)
            .values(room_id=room.id, date=d, status=AvailabilityStatus.booked)
            .on_conflict_do_update(
                index_elements=["room_id", "date"],
                set_={"status": AvailabilityStatus.booked},
            )
        )
        await db.execute(stmt)

    client = await _find_or_create_client_for_walkin(db, payload)

    for _ in range(5):
        code = gen_booking_code()
        clash = (
            await db.execute(select(Booking.id).where(Booking.code == code))
        ).scalar_one_or_none()
        if clash is None:
            break
    else:
        raise APIError(500, "internal", "Failed to generate booking code")

    booking = Booking(
        code=code,
        client_id=client.id,
        room_id=room.id,
        check_in=payload.check_in,
        check_out=payload.check_out,
        guests=payload.guests,
        total_kgs=total,
        status=BookingStatus.pending,
        postpay=True,    # walk-in = физ. оплата у стойки; не подпадает под 24h auto-cancel
        confirmed=True,  # партнёр сам её создал — гарантия приёма автоматически
    )
    db.add(booking)
    hotel_id_for_pub = hotel.id
    owner_id_snap = hotel.owner_user_id
    await db.commit()
    await db.refresh(booking)
    await pubsub.publish_refresh(hotel_id_for_pub)
    await audit(
        db, ctx,
        owner_user_id=owner_id_snap,
        action="walkin.create",
        subject_type="booking",
        subject_id=booking.id,
        payload={
            "code": booking.code,
            "hotel_id": hotel_id_for_pub,
            "room_id": room.id,
            "check_in": str(booking.check_in),
            "check_out": str(booking.check_out),
            "total_kgs": booking.total_kgs,
        },
    )
    return _to_partner_booking(booking, room, hotel, client)


# ─── /p/rooms (flat list with today_status) ────────────────────────────────

@router.get("/rooms", response_model=list[RoomFlatView])
async def list_all_my_rooms(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    today = date.today()
    rows = (
        await db.execute(
            select(Room, Hotel)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Hotel.owner_user_id.in_(accessible_ids))
            .order_by(Hotel.name_ru, Room.id)
        )
    ).all()
    if not rows:
        return []

    room_ids = [r.id for r, _ in rows]
    av_rows = (
        await db.execute(
            select(Availability)
            .where(Availability.room_id.in_(room_ids), Availability.date == today)
        )
    ).scalars().all()
    today_by_room = {a.room_id: a.status for a in av_rows}

    out: list[RoomFlatView] = []
    for r, h in rows:
        photo = (r.photos or [None])[0] if r.photos else None
        out.append(RoomFlatView(
            room_id=r.id,
            room_name_ru=r.name_ru,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            capacity=r.capacity,
            beds=r.beds,
            floor=r.floor,
            price_kgs=r.price_kgs,
            today_status=today_by_room.get(r.id, AvailabilityStatus.free),
            photo=photo,
        ))
    return out


# ─── /p/clients ────────────────────────────────────────────────────────────

def _to_client_view(c: Client, bookings_count: int, last_date: date | None) -> ClientPartnerView:
    return ClientPartnerView(
        id=c.id,
        user_id=c.user_id,
        first_name=c.first_name,
        last_name=c.last_name,
        phone=c.phone,
        email=c.email,
        doc_kind=c.doc_kind,
        doc_number=c.doc_number,
        photo_url=c.photo_url,
        bookings_count=bookings_count,
        last_booking_date=last_date,
        created_at=c.created_at,
    )


@router.get("/clients", response_model=list[ClientPartnerView])
async def list_my_clients(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """All clients who have at least one booking in any of my accessible
    owners' hotels (optionally scoped to one ?owner_id=)."""
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    from sqlalchemy import func as sa_func
    stmt = (
        select(
            Client,
            sa_func.count(Booking.id).label("cnt"),
            sa_func.max(Booking.check_in).label("last_date"),
        )
        .join(Booking, Booking.client_id == Client.id)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .where(Hotel.owner_user_id.in_(accessible_ids))
        .group_by(Client.id)
        .order_by(sa_func.max(Booking.created_at).desc())
        .limit(500)
    )
    rows = (await db.execute(stmt)).all()
    return [_to_client_view(c, cnt, last) for (c, cnt, last) in rows]


@router.post("/clients/lookup", response_model=ClientPartnerView | None)
async def lookup_client(
    payload: ClientLookup,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """For the walk-in form: find existing client by phone or email so the
    partner can pre-fill. Returns the global record (scope-agnostic). Returns
    null if nothing matched."""
    norm_phone = normalize_phone(payload.phone)
    norm_email = normalize_email(payload.email)
    if not norm_phone and not norm_email:
        return None
    c: Client | None = None
    if norm_phone:
        c = (await db.execute(select(Client).where(Client.phone == norm_phone))).scalar_one_or_none()
    if c is None and norm_email:
        c = (await db.execute(select(Client).where(Client.email == norm_email))).scalar_one_or_none()
    if c is None:
        return None
    return _to_client_view(c, bookings_count=0, last_date=None)


@router.get("/clients/{client_id}", response_model=ClientPartnerView)
async def get_my_client(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
    accessible_ids = list(ctx.accessible_owners.keys())
    from sqlalchemy import func as sa_func
    cnt, last = (
        await db.execute(
            select(sa_func.count(Booking.id), sa_func.max(Booking.check_in))
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Booking.client_id == c.id, Hotel.owner_user_id.in_(accessible_ids))
        )
    ).one()
    return _to_client_view(c, cnt or 0, last)


@router.get("/clients/{client_id}/bookings", response_model=list[PartnerBookingView])
async def list_my_client_bookings(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
    accessible_ids = list(ctx.accessible_owners.keys())
    rows = (
        await db.execute(
            select(Booking, Room, Hotel)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Booking.client_id == c.id, Hotel.owner_user_id.in_(accessible_ids))
            .order_by(Booking.created_at.desc())
        )
    ).all()
    return [_to_partner_booking(b, r, h, c) for (b, r, h) in rows]


@router.put("/clients/{client_id}", response_model=ClientPartnerView)
async def update_my_client(
    client_id: int,
    payload: ClientUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await scope.get_my_client(db, ctx, client_id)
    # Allow edit if user has manage_bookings on ANY accessible owner where the
    # client has bookings. Client records are global (one row), so this is the
    # cleanest gate that doesn't require per-owner forking.
    owner_ids_with_bookings = set(
        (
            await db.execute(
                select(Hotel.owner_user_id)
                .join(Room, Room.hotel_id == Hotel.id)
                .join(Booking, Booking.room_id == Room.id)
                .where(Booking.client_id == c.id)
                .distinct()
            )
        ).scalars()
    )
    has_perm = any(
        oid in ctx.accessible_owners and ctx.accessible_owners[oid].has("manage_bookings")
        for oid in owner_ids_with_bookings
    )
    if not has_perm:
        raise APIError(403, "permission_denied", "Missing permission: manage_bookings")
    data = payload.model_dump(exclude_unset=True)
    if "phone" in data:
        data["phone"] = normalize_phone(data["phone"])
    if "email" in data:
        data["email"] = normalize_email(data["email"])
    for k, v in data.items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    await audit(
        db, ctx,
        owner_user_id=next(iter(owner_ids_with_bookings & set(ctx.accessible_owners.keys()))),
        action="client.update",
        subject_type="client",
        subject_id=c.id,
        payload=data,
    )
    return _to_client_view(c, bookings_count=0, last_date=None)


# ─── Staff ────────────────────────────────────────────────────────────────

def _ps_to_perms(ps: PartnerStaff) -> StaffPerms:
    return StaffPerms(
        manage_hotel=ps.perm_manage_hotel,
        manage_rooms=ps.perm_manage_rooms,
        manage_bookings=ps.perm_manage_bookings,
        manage_staff=ps.perm_manage_staff,
    )


def _ps_to_view(ps: PartnerStaff, staff_user: User) -> StaffView:
    return StaffView(
        id=ps.id,
        owner_user_id=ps.owner_user_id,
        staff_user_id=ps.staff_user_id,
        staff_telegram_id=staff_user.telegram_id,
        staff_display_name=staff_user.first_name,
        perms=_ps_to_perms(ps),
        note=ps.note,
        created_at=ps.created_at,
    )


@router.get("/staff", response_model=list[StaffView])
async def list_staff(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    # Default: list staff for the owner-self (if any) — staff can request
    # explicitly by owner_id (must have manage_staff there).
    if owner_id is None:
        self_access = ctx.accessible_owners.get(ctx.user.id)
        if self_access is None or not self_access.is_self:
            raise APIError(400, "bad_request", "owner_id is required")
        owner_id = ctx.user.id
    access = ctx.accessible_owners.get(owner_id)
    if access is None:
        raise APIError(404, "not_found", "Owner not accessible")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")

    rows = (
        await db.execute(
            select(PartnerStaff, User)
            .join(User, User.id == PartnerStaff.staff_user_id)
            .where(PartnerStaff.owner_user_id == owner_id)
            .order_by(PartnerStaff.created_at.desc())
        )
    ).all()
    return [_ps_to_view(ps, u) for (ps, u) in rows]


@router.post("/staff", response_model=StaffView, status_code=201)
async def add_staff(
    payload: StaffCreate,
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    if owner_id is None:
        self_access = ctx.accessible_owners.get(ctx.user.id)
        if self_access is None or not self_access.is_self:
            raise APIError(400, "bad_request", "owner_id is required")
        owner_id = ctx.user.id
    access = ctx.accessible_owners.get(owner_id)
    if access is None:
        raise APIError(404, "not_found", "Owner not accessible")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")

    if payload.telegram_id == 0:
        raise APIError(400, "bad_request", "Invalid telegram_id")

    # Look up or create the user stub.
    staff_user = (
        await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    ).scalar_one_or_none()
    if staff_user is None:
        staff_user = User(
            telegram_id=payload.telegram_id,
            role=UserRole.partner,
        )
        db.add(staff_user)
        await db.flush()
    elif staff_user.role == UserRole.admin:
        raise APIError(409, "incompatible_role", "Cannot add admin as staff")
    elif staff_user.role == UserRole.client:
        staff_user.role = UserRole.partner  # upgrade

    if staff_user.id == owner_id:
        raise APIError(400, "bad_request", "Cannot add yourself as your own staff")

    existing = (
        await db.execute(
            select(PartnerStaff).where(
                PartnerStaff.owner_user_id == owner_id,
                PartnerStaff.staff_user_id == staff_user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise APIError(409, "already_member", "User is already a staff member")

    ps = PartnerStaff(
        owner_user_id=owner_id,
        staff_user_id=staff_user.id,
        perm_manage_hotel=payload.perms.manage_hotel,
        perm_manage_rooms=payload.perms.manage_rooms,
        perm_manage_bookings=payload.perms.manage_bookings,
        perm_manage_staff=payload.perms.manage_staff,
        note=payload.note,
        added_by_user_id=ctx.user.id,
    )
    db.add(ps)
    await db.commit()
    await db.refresh(ps)
    await audit(
        db, ctx,
        owner_user_id=owner_id,
        action="staff.add",
        subject_type="staff",
        subject_id=ps.id,
        payload={
            "staff_user_id": staff_user.id,
            "telegram_id": staff_user.telegram_id,
            "perms": payload.perms.model_dump(),
            "note": payload.note,
        },
    )
    return _ps_to_view(ps, staff_user)


@router.put("/staff/{staff_id}", response_model=StaffView)
async def update_staff(
    staff_id: int,
    payload: StaffUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(PartnerStaff, User)
            .join(User, User.id == PartnerStaff.staff_user_id)
            .where(PartnerStaff.id == staff_id)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Staff member not found")
    ps, staff_user = row
    access = ctx.accessible_owners.get(ps.owner_user_id)
    if access is None:
        raise APIError(404, "not_found", "Staff member not found")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")

    diff: dict = {}
    if payload.perms is not None:
        before = _ps_to_perms(ps).model_dump()
        ps.perm_manage_hotel = payload.perms.manage_hotel
        ps.perm_manage_rooms = payload.perms.manage_rooms
        ps.perm_manage_bookings = payload.perms.manage_bookings
        ps.perm_manage_staff = payload.perms.manage_staff
        after = payload.perms.model_dump()
        if before != after:
            diff["perms"] = {"before": before, "after": after}
    if payload.note is not None and payload.note != ps.note:
        diff["note"] = {"before": ps.note, "after": payload.note}
        ps.note = payload.note
    await db.commit()
    await db.refresh(ps)
    if diff:
        await audit(
            db, ctx,
            owner_user_id=ps.owner_user_id,
            action="staff.update",
            subject_type="staff",
            subject_id=ps.id,
            payload=diff,
        )
    return _ps_to_view(ps, staff_user)


@router.delete("/staff/{staff_id}", status_code=204)
async def remove_staff(
    staff_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    ps = (
        await db.execute(select(PartnerStaff).where(PartnerStaff.id == staff_id))
    ).scalar_one_or_none()
    if ps is None:
        raise APIError(404, "not_found", "Staff member not found")
    access = ctx.accessible_owners.get(ps.owner_user_id)
    if access is None:
        raise APIError(404, "not_found", "Staff member not found")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")

    owner_id = ps.owner_user_id
    snapshot = {
        "staff_user_id": ps.staff_user_id,
        "perms": _ps_to_perms(ps).model_dump(),
        "note": ps.note,
    }
    await db.delete(ps)
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=owner_id,
        action="staff.remove",
        subject_type="staff",
        subject_id=staff_id,
        payload=snapshot,
    )
    return None


# ─── Staff invite (внешние ссылки) ────────────────────────────────────────

def _invite_to_view(inv: PartnerStaffInvite) -> StaffInviteView:
    return StaffInviteView(
        id=inv.id,
        owner_user_id=inv.owner_user_id,
        token=inv.token,
        url=f"https://t.me/{settings.tg_bot_username}?startapp=invite_{inv.token}",
        perms=StaffPerms(
            manage_hotel=inv.perm_manage_hotel,
            manage_rooms=inv.perm_manage_rooms,
            manage_bookings=inv.perm_manage_bookings,
            manage_staff=inv.perm_manage_staff,
        ),
        note=inv.note,
        expires_at=inv.expires_at,
        used_at=inv.used_at,
        created_at=inv.created_at,
    )


@router.post("/staff/invites", response_model=StaffInviteView, status_code=201)
async def create_staff_invite(
    payload: StaffInviteCreate,
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    if owner_id is None:
        self_access = ctx.accessible_owners.get(ctx.user.id)
        if self_access is None or not self_access.is_self:
            raise APIError(400, "bad_request", "owner_id is required")
        owner_id = ctx.user.id
    access = ctx.accessible_owners.get(owner_id)
    if access is None:
        raise APIError(404, "not_found", "Owner not accessible")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")

    token = secrets.token_hex(24)
    expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
    inv = PartnerStaffInvite(
        token=token,
        owner_user_id=owner_id,
        created_by_user_id=ctx.user.id,
        perm_manage_hotel=payload.perms.manage_hotel,
        perm_manage_rooms=payload.perms.manage_rooms,
        perm_manage_bookings=payload.perms.manage_bookings,
        perm_manage_staff=payload.perms.manage_staff,
        note=payload.note,
        expires_at=expires_at,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    await audit(
        db, ctx,
        owner_user_id=owner_id,
        action="staff.invite_create",
        subject_type="staff_invite",
        subject_id=inv.id,
        payload={
            "perms": payload.perms.model_dump(),
            "expires_at": inv.expires_at.isoformat(),
            "note": payload.note,
        },
    )
    return _invite_to_view(inv)


@router.get("/staff/invites", response_model=list[StaffInviteView])
async def list_staff_invites(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    # Только владельцы, где у текущего user есть manage_staff.
    allowed = [
        oid for oid in accessible_ids
        if ctx.accessible_owners[oid].has("manage_staff")
    ]
    if not allowed:
        return []
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(PartnerStaffInvite)
            .where(PartnerStaffInvite.owner_user_id.in_(allowed))
            .where(PartnerStaffInvite.used_at.is_(None))
            .where(PartnerStaffInvite.expires_at > now)
            .order_by(PartnerStaffInvite.created_at.desc())
        )
    ).scalars().all()
    return [_invite_to_view(r) for r in rows]


@router.delete("/staff/invites/{invite_id}", status_code=204)
async def revoke_staff_invite(
    invite_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    inv = (
        await db.execute(
            select(PartnerStaffInvite).where(PartnerStaffInvite.id == invite_id)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise APIError(404, "not_found", "Invite not found")
    access = ctx.accessible_owners.get(inv.owner_user_id)
    if access is None or not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")
    if inv.used_at is not None:
        # already revoked or used — idempotent
        return None
    inv.used_at = datetime.now(timezone.utc)  # marks as inactive without consumer
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=inv.owner_user_id,
        action="staff.invite_revoke",
        subject_type="staff_invite",
        subject_id=inv.id,
        payload={"token_prefix": inv.token[:8]},
    )
    return None


@router.post("/staff/invite/accept", response_model=StaffView, status_code=201)
async def accept_staff_invite(
    payload: StaffInviteAccept,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Любой авторизованный пользователь принимает invite. После accept
    становится partner-staff заданного owner с perms из invite."""
    inv = (
        await db.execute(
            select(PartnerStaffInvite).where(PartnerStaffInvite.token == payload.token)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise APIError(404, "not_found", "Invite not found")
    now = datetime.now(timezone.utc)
    if inv.used_at is not None:
        raise APIError(410, "invite_used", "Invite already used or revoked")
    if inv.expires_at <= now:
        raise APIError(410, "invite_expired", "Invite has expired")
    if ctx.user.id == inv.owner_user_id:
        raise APIError(400, "bad_request", "Cannot accept own invite")
    if ctx.user.role == UserRole.admin:
        raise APIError(409, "incompatible_role", "Cannot add admin as staff")

    existing = (
        await db.execute(
            select(PartnerStaff).where(
                PartnerStaff.owner_user_id == inv.owner_user_id,
                PartnerStaff.staff_user_id == ctx.user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise APIError(409, "already_member", "Already a staff member of this owner")

    if ctx.user.role == UserRole.client:
        ctx.user.role = UserRole.partner

    ps = PartnerStaff(
        owner_user_id=inv.owner_user_id,
        staff_user_id=ctx.user.id,
        perm_manage_hotel=inv.perm_manage_hotel,
        perm_manage_rooms=inv.perm_manage_rooms,
        perm_manage_bookings=inv.perm_manage_bookings,
        perm_manage_staff=inv.perm_manage_staff,
        note=inv.note,
        added_by_user_id=inv.created_by_user_id,
    )
    db.add(ps)
    inv.used_at = now
    inv.used_by_user_id = ctx.user.id
    await db.commit()
    await db.refresh(ps)
    # audit is recorded under the owner's namespace
    fake_ctx = AuthContext(user=ctx.user, session=ctx.session)
    fake_ctx.accessible_owners = {
        inv.owner_user_id: type("X", (), {"is_self": False})()  # unused; we pass owner_user_id explicitly
    }
    # Use a direct audit insert to set actor_role = "staff" (self-onboarding).
    db.add(AuditLog(
        owner_user_id=inv.owner_user_id,
        actor_user_id=ctx.user.id,
        actor_role="staff",
        action="staff.invite_accept",
        subject_type="staff",
        subject_id=ps.id,
        payload={
            "invite_id": inv.id,
            "perms": {
                "manage_hotel": inv.perm_manage_hotel,
                "manage_rooms": inv.perm_manage_rooms,
                "manage_bookings": inv.perm_manage_bookings,
                "manage_staff": inv.perm_manage_staff,
            },
        },
    ))
    await db.commit()
    return _ps_to_view(ps, ctx.user)


# ─── Audit log read ───────────────────────────────────────────────────────

def _audit_stmt_base(
    ctx: AuthContext,
    owner_id: int | None,
    action_filter: str | None,
    subject_type_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    q: str | None,
    actor_user_id: int | None,
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    stmt = (
        select(AuditLog, User)
        .join(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.owner_user_id.in_(accessible_ids))
        .order_by(AuditLog.created_at.desc())
    )
    if action_filter:
        stmt = stmt.where(AuditLog.action == action_filter)
    if subject_type_filter:
        stmt = stmt.where(AuditLog.subject_type == subject_type_filter)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    if actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if q:
        pat = f"%{q}%"
        stmt = stmt.where(
            (User.first_name.ilike(pat))
            | (AuditLog.action.ilike(pat))
            | (AuditLog.subject_type.ilike(pat))
        )
    return stmt


@router.get("/audit", response_model=list[AuditEntryView])
async def list_audit(
    owner_id: int | None = Query(default=None),
    action_filter: str | None = Query(default=None, alias="action"),
    subject_type_filter: str | None = Query(default=None, alias="subject_type"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=64),
    actor_user_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    stmt = _audit_stmt_base(
        ctx, owner_id, action_filter, subject_type_filter, since, until, q, actor_user_id
    ).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [
        AuditEntryView(
            id=a.id,
            owner_user_id=a.owner_user_id,
            actor_user_id=a.actor_user_id,
            actor_display_name=u.first_name,
            actor_role=a.actor_role,
            action=a.action,
            subject_type=a.subject_type,
            subject_id=a.subject_id,
            payload=a.payload,
            created_at=a.created_at,
        )
        for (a, u) in rows
    ]


@router.get("/audit.csv")
async def audit_csv(
    owner_id: int | None = Query(default=None),
    action_filter: str | None = Query(default=None, alias="action"),
    subject_type_filter: str | None = Query(default=None, alias="subject_type"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=64),
    actor_user_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    import csv
    import io
    import json as _json
    from fastapi.responses import StreamingResponse

    stmt = _audit_stmt_base(
        ctx, owner_id, action_filter, subject_type_filter, since, until, q, actor_user_id
    )
    rows = (await db.execute(stmt)).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at", "actor", "actor_role", "action", "subject_type", "subject_id", "payload"])
    for (a, u) in rows:
        w.writerow([
            a.created_at.isoformat(),
            u.first_name or "",
            a.actor_role,
            a.action,
            a.subject_type or "",
            a.subject_id if a.subject_id is not None else "",
            _json.dumps(a.payload, ensure_ascii=False) if a.payload is not None else "",
        ])
    today = datetime.now(timezone.utc).date().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit-{today}.csv"'},
    )
